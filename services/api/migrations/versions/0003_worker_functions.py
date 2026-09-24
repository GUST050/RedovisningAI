"""Funktion för bakgrundsjobb: lista byråers id (inga övriga data).

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE FUNCTION worker_org_ids() RETURNS SETOF uuid
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$ SELECT id FROM organization $$;
    REVOKE ALL ON FUNCTION worker_org_ids() FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION worker_org_ids() TO redovisningai_app;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION worker_org_ids()")
