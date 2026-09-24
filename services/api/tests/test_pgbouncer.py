"""RLS-kontexten genom PgBouncer i transaktionsläge (en delad serveranslutning).

Visar både att vår metod (set_config(..., true) i transaktion) är säker och – som negativ
kontroll – att sessionsnivå-SET faktiskt läcker mellan klienter i den här uppsättningen.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from redovisningai.db.bootstrap import create_company, create_organization
from redovisningai.db.session import TenantContext

pytestmark = pytest.mark.usefixtures("database")
PORT = 64329


@pytest.fixture(scope="module")
def bouncer(database: str) -> Iterator[str]:  # type: ignore[no-untyped-def]
    exe = shutil.which("pgbouncer") or "/usr/sbin/pgbouncer"
    if not Path(exe).exists():
        pytest.skip("pgbouncer saknas")
    d = Path(tempfile.mkdtemp(prefix="rai-bouncer-"))
    (d / "users.txt").write_text('"redovisningai_app" "app"\n')
    (d / "pgbouncer.ini").write_text(f"""[databases]
rai_test = host=127.0.0.1 port={os.environ.get("RAI_TEST_PG_PORT", "54329")} dbname=rai_test
[pgbouncer]
listen_addr = 127.0.0.1
listen_port = {PORT}
auth_type = trust
auth_file = {d}/users.txt
pool_mode = transaction
default_pool_size = 1
max_client_conn = 10
server_reset_query =
unix_socket_dir =
""")
    cmd = [exe, str(d / "pgbouncer.ini")]
    if os.geteuid() == 0:
        subprocess.run(["chown", "-R", "postgres", str(d)], check=True)
        cmd = ["runuser", "-u", "postgres", "--", *cmd]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            psycopg.connect(
                f"host=127.0.0.1 port={PORT} dbname=rai_test user=redovisningai_app", connect_timeout=1
            ).close()
            break
        except Exception:
            time.sleep(0.1)
    yield f"host=127.0.0.1 port={PORT} dbname=rai_test user=redovisningai_app"
    proc.terminate()


@pytest.fixture(scope="module")
def org(database: str):  # type: ignore[no-untyped-def]
    o = create_organization("Bouncer-byrån", "b@bouncer.se", "B", owner_url=database)
    ctx = TenantContext(o.org_id, o.admin_user_id, "ADMIN", user_email="b@bouncer.se")
    create_company(ctx, "Pool AB", "556000-9999", assign_to=[o.admin_user_id])
    return o


def _count(conn: psycopg.Connection) -> int:
    return conn.execute("select count(*) from company where name = 'Pool AB'").fetchone()[0]  # type: ignore[index]


def test_transaction_local_context_does_not_leak(bouncer: str, org) -> None:  # type: ignore[no-untyped-def]
    a = psycopg.connect(bouncer, autocommit=True)
    b = psycopg.connect(bouncer, autocommit=True)
    with a.transaction():
        a.execute(
            "select set_config('app.org_id', %s, true), set_config('app.role', 'ADMIN', true)", (str(org.org_id),)
        )
        assert _count(a) == 1
    assert _count(b) == 0  # samma serveranslutning, ingen kontext
    # Avbruten transaktion lämnar inte heller något kvar.
    with pytest.raises(psycopg.errors.DivisionByZero), a.transaction():
        a.execute(
            "select set_config('app.org_id', %s, true), set_config('app.role', 'ADMIN', true)", (str(org.org_id),)
        )
        a.execute("select 1/0")
    assert _count(b) == 0


def test_negative_control_session_set_leaks(bouncer: str, org) -> None:  # type: ignore[no-untyped-def]
    a = psycopg.connect(bouncer, autocommit=True)
    b = psycopg.connect(bouncer, autocommit=True)
    a.execute("select set_config('app.org_id', %s, false), set_config('app.role', 'ADMIN', false)", (str(org.org_id),))
    try:
        assert _count(b) == 1, "Sessionsnivå-SET ska läcka i transaktionsläge – annars testar vi inget"
    finally:
        a.execute("reset all")
