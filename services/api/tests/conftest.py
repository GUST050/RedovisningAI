"""Gemensamma fixtures. Databastester kör mot en egen testdatabas (rai_test) med
applikationsrollen – precis som i produktion – så att RLS verkligen testas."""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

import pytest

PG_HOST = os.environ.get("RAI_TEST_PG_HOST", "127.0.0.1")
PG_PORT = os.environ.get("RAI_TEST_PG_PORT", "54329")
PG_ADMIN = os.environ.get("RAI_TEST_PG_ADMIN_URL", f"postgresql+psycopg://postgres@{PG_HOST}:{PG_PORT}/postgres")
TEST_DB = "rai_test"
OWNER_URL = PG_ADMIN.rsplit("/", 1)[0] + f"/{TEST_DB}"
APP_PASSWORD = quote(os.environ.get("RAI_TEST_APP_DB_PASSWORD", "app"), safe="")
APP_URL = f"postgresql+psycopg://redovisningai_app:{APP_PASSWORD}@{PG_HOST}:{PG_PORT}/{TEST_DB}"

os.environ["RAI_DATABASE_URL_OWNER"] = OWNER_URL
os.environ["RAI_DATABASE_URL"] = APP_URL
os.environ["RAI_STORAGE_PATH"] = tempfile.mkdtemp(prefix="rai-storage-")
os.environ["RAI_ENV"] = "test"
# Testerna kontrollerar att oinloggade anrop nekas – ingen automatisk standardanvändare.
os.environ["RAI_DEV_DEFAULT_USER"] = ""


def _pg_available() -> bool:
    from sqlalchemy import create_engine, text

    try:
        with create_engine(PG_ADMIN).connect() as c:
            c.execute(text("select 1"))
        return True
    except Exception:
        script = Path(__file__).parent.parent / "scripts" / "dev-postgres.sh"
        if script.exists() and not os.environ.get("CI"):
            subprocess.run([str(script), "start"], check=False, capture_output=True)
            try:
                with create_engine(PG_ADMIN).connect() as c:
                    c.execute(text("select 1"))
                return True
            except Exception:
                return False
        return False


@pytest.fixture(scope="session")
def database() -> Iterator[str]:
    if not _pg_available():
        if os.environ.get("CI"):
            pytest.fail("PostgreSQL krävs i CI – databastesterna får inte hoppas över")
        pytest.skip("PostgreSQL saknas – kör scripts/dev-postgres.sh start")
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    admin = create_engine(PG_ADMIN, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)"))
        c.execute(text(f"CREATE DATABASE {TEST_DB}"))
    admin.dispose()
    cfg = Config(str(Path(__file__).parent.parent / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).parent.parent / "migrations"))
    cfg.attributes["url"] = OWNER_URL
    command.upgrade(cfg, "head")
    yield OWNER_URL
