"""Godkännande per kund av utökat AI-underlag (plan §9.10).

Tabellen får samma RLS-policyer som kundtabellerna i 0002. Den står inte i COMPANY_TABLES:
0002 läser listan och körs före denna migration på en ny databas.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

APP_ROLE = "redovisningai_app"
TABLE = "ai_data_approval"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("org_id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("data_types", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=False),
        sa.Column("approved_by", sa.String(length=320), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("revoked_by", sa.String(length=320), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("valid_to >= valid_from", name="ck_ai_data_approval_valid_range"),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_data_approval_company_id"), TABLE, ["company_id"], unique=False)
    op.create_index(op.f("ix_ai_data_approval_org_id"), TABLE, ["org_id"], unique=False)

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {TABLE} TO {APP_ROLE}")
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    # Exakt samma uttryck som kundtabellerna i 0002.
    op.execute(
        f"CREATE POLICY t_read ON {TABLE} FOR SELECT USING (org_id = app_org() AND app_company_visible(company_id))"
    )
    op.execute(
        f"CREATE POLICY t_write ON {TABLE} FOR ALL USING (org_id = app_org() AND "
        f"app_company_visible(company_id) AND app_can_write()) WITH CHECK (org_id = app_org() AND "
        f"app_company_visible(company_id) AND app_can_write())"
    )


def downgrade() -> None:
    op.drop_table(TABLE)  # tar även bort index och policyer
