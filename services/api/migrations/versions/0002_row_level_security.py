"""Row Level Security, applikationsroll och säkerhetsfunktioner.

Skydd i två nivåer: applikationen kontrollerar behörighet, och databasen begränsar vilka
rader en förfrågan kan se även om ett API-filter skulle bli fel.

Kontext sätts per transaktion med set_config(..., true) (motsvarar SET LOCAL – säkert bakom
PgBouncer i transaktionsläge):
  app.org_id, app.user_id, app.role (ADMIN|CONSULTANT|VIEWER|WORKER|PUBLIC_QUESTION),
  app.payroll ('on'/'off'), app.aml ('on'/'off'), app.question_id

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import os

from alembic import op

from redovisningai.db.models import COMPANY_TABLES, OPTIONAL_COMPANY_TABLES

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

APP_ROLE = "redovisningai_app"


def upgrade() -> None:
    password = os.environ.get("RAI_APP_DB_PASSWORD", "app").replace("'", "''")
    op.execute(f"""
    DO $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
        CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{password}' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
      END IF;
    END $$;
    """)
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
    op.execute(f"REVOKE ALL ON alembic_version FROM {APP_ROLE}")
    # Revisionsloggen kan bara läggas till och läsas.
    op.execute(f"REVOKE UPDATE, DELETE ON audit_event FROM {APP_ROLE}")

    # ------------------------------------------------------------------ hjälpfunktioner
    op.execute("""
    CREATE FUNCTION app_org() RETURNS uuid LANGUAGE sql STABLE AS
      $$ SELECT nullif(current_setting('app.org_id', true), '')::uuid $$;
    CREATE FUNCTION app_user_id() RETURNS uuid LANGUAGE sql STABLE AS
      $$ SELECT nullif(current_setting('app.user_id', true), '')::uuid $$;
    CREATE FUNCTION app_role() RETURNS text LANGUAGE sql STABLE AS
      $$ SELECT coalesce(nullif(current_setting('app.role', true), ''), 'NONE') $$;
    CREATE FUNCTION app_flag(flag text) RETURNS boolean LANGUAGE sql STABLE AS
      $$ SELECT coalesce(current_setting('app.' || flag, true), 'off') = 'on' $$;
    CREATE FUNCTION app_company_visible(cid uuid) RETURNS boolean LANGUAGE sql STABLE AS $$
      SELECT app_role() IN ('ADMIN', 'WORKER')
          OR (app_role() IN ('CONSULTANT', 'VIEWER') AND EXISTS (
                SELECT 1 FROM company_assignment ca WHERE ca.company_id = cid AND ca.user_id = app_user_id()))
    $$;
    CREATE FUNCTION app_can_write() RETURNS boolean LANGUAGE sql STABLE AS
      $$ SELECT app_role() IN ('ADMIN', 'CONSULTANT', 'WORKER') $$;
    """)

    # Inloggning: hitta användare och medlemskap innan byråkontext finns (SECURITY DEFINER,
    # returnerar bara användarens egna medlemskap).
    op.execute(f"""
    CREATE FUNCTION auth_memberships(p_subject text)
    RETURNS TABLE(user_id uuid, email text, name text, org_id uuid, org_name text, role text,
                  can_payroll boolean, can_aml boolean, can_approve_reports boolean)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
      SELECT u.id, u.email, u.name, m.org_id, o.name, m.role, m.can_payroll, m.can_aml, m.can_approve_reports
        FROM app_user u
        JOIN organization_membership m ON m.user_id = u.id
        JOIN organization o ON o.id = m.org_id
       WHERE u.idp_subject = p_subject
    $$;
    REVOKE ALL ON FUNCTION auth_memberships(text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION auth_memberships(text) TO {APP_ROLE};

    -- Publik svarslänk för kundfrågor: slå upp fråga via hash av token (ingen annan data).
    CREATE FUNCTION question_lookup(p_token_hash text)
    RETURNS TABLE(id uuid, org_id uuid, company_id uuid)
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
      SELECT q.id, q.org_id, q.company_id FROM client_question q
       WHERE q.token_hash = p_token_hash AND q.expires_at > now() AND q.status IN ('SENT', 'ANSWERED')
    $$;
    REVOKE ALL ON FUNCTION question_lookup(text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION question_lookup(text) TO {APP_ROLE};
    """)

    def enable(table: str) -> None:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # ------------------------------------------------------------------ byrå och användare
    enable("organization")
    op.execute("CREATE POLICY org_read ON organization FOR SELECT USING (id = app_org())")
    op.execute(
        "CREATE POLICY org_write ON organization FOR UPDATE USING (id = app_org() AND app_role() = 'ADMIN')"
        " WITH CHECK (id = app_org())"
    )

    enable("organization_membership")
    op.execute("CREATE POLICY m_read ON organization_membership FOR SELECT USING (org_id = app_org())")
    op.execute(
        "CREATE POLICY m_write ON organization_membership FOR ALL USING (org_id = app_org() AND "
        "app_role() = 'ADMIN') WITH CHECK (org_id = app_org() AND app_role() = 'ADMIN')"
    )

    enable("app_user")
    op.execute("""CREATE POLICY u_read ON app_user FOR SELECT USING (
        id = app_user_id() OR EXISTS (SELECT 1 FROM organization_membership m
                                       WHERE m.user_id = app_user.id AND m.org_id = app_org()))""")
    op.execute(
        "CREATE POLICY u_write ON app_user FOR ALL USING (app_role() = 'ADMIN' OR id = app_user_id()) "
        "WITH CHECK (app_role() = 'ADMIN' OR id = app_user_id())"
    )

    enable("company")
    op.execute("CREATE POLICY c_read ON company FOR SELECT USING (org_id = app_org() AND app_company_visible(id))")
    op.execute("CREATE POLICY c_insert ON company FOR INSERT WITH CHECK (org_id = app_org() AND app_can_write())")
    op.execute(
        "CREATE POLICY c_update ON company FOR UPDATE USING (org_id = app_org() AND app_company_visible(id) "
        "AND app_can_write()) WITH CHECK (org_id = app_org())"
    )
    op.execute("CREATE POLICY c_delete ON company FOR DELETE USING (org_id = app_org() AND app_role() = 'ADMIN')")

    enable("company_assignment")
    op.execute(
        "CREATE POLICY ca_read ON company_assignment FOR SELECT USING (org_id = app_org() AND "
        "(app_role() IN ('ADMIN', 'WORKER') OR user_id = app_user_id()))"
    )
    op.execute(
        "CREATE POLICY ca_write ON company_assignment FOR ALL USING (org_id = app_org() AND "
        "(app_role() IN ('ADMIN', 'WORKER') OR (app_role() = 'CONSULTANT' AND user_id = app_user_id()))) "
        "WITH CHECK (org_id = app_org() AND (app_role() IN ('ADMIN', 'WORKER') OR "
        "(app_role() = 'CONSULTANT' AND user_id = app_user_id())))"
    )

    # ------------------------------------------------------------------ kunddata per klient
    for t in [x for x in COMPANY_TABLES if x != "company_assignment"]:
        enable(t)
        op.execute(
            f"CREATE POLICY t_read ON {t} FOR SELECT USING (org_id = app_org() AND app_company_visible(company_id))"
        )
        op.execute(
            f"CREATE POLICY t_write ON {t} FOR ALL USING (org_id = app_org() AND "
            f"app_company_visible(company_id) AND app_can_write()) WITH CHECK (org_id = app_org() AND "
            f"app_company_visible(company_id) AND app_can_write())"
        )

    for t in OPTIONAL_COMPANY_TABLES:
        enable(t)
        op.execute(
            f"CREATE POLICY t_read ON {t} FOR SELECT USING (org_id = app_org() AND "
            f"(company_id IS NULL OR app_company_visible(company_id)))"
        )
        op.execute(
            f"CREATE POLICY t_write ON {t} FOR ALL USING (org_id = app_org() AND app_can_write() AND "
            f"((company_id IS NULL AND app_role() IN ('ADMIN', 'WORKER')) OR "
            f"(company_id IS NOT NULL AND app_company_visible(company_id)))) "
            f"WITH CHECK (org_id = app_org() AND app_can_write() AND "
            f"((company_id IS NULL AND app_role() IN ('ADMIN', 'WORKER')) OR "
            f"(company_id IS NOT NULL AND app_company_visible(company_id))))"
        )

    # Lönerader på radnivå kräver behörigheten Lönedata (app.payroll = on).
    op.execute("""CREATE POLICY payroll_rows ON transaction_row AS RESTRICTIVE FOR SELECT USING (
        app_flag('payroll') OR NOT (account BETWEEN 7000 AND 7699 OR account BETWEEN 2710 AND 2719))""")
    # PTL-fynd och PTL-bedömningar kräver behörigheten PTL-ansvarig (app.aml = on).
    for t in ("finding", "case_record"):
        op.execute(
            f"CREATE POLICY aml_only ON {t} AS RESTRICTIVE FOR ALL USING (visibility <> 'RESTRICTED_AML' "
            f"OR app_flag('aml')) WITH CHECK (visibility <> 'RESTRICTED_AML' OR app_flag('aml'))"
        )
    op.execute(
        "CREATE POLICY aml_only ON aml_assessment AS RESTRICTIVE FOR ALL USING (app_flag('aml')) "
        "WITH CHECK (app_flag('aml'))"
    )

    # Publik svarssida: bara den enskilda frågan.
    op.execute("""CREATE POLICY q_public_read ON client_question FOR SELECT USING (
        app_role() = 'PUBLIC_QUESTION' AND org_id = app_org()
        AND id = nullif(current_setting('app.question_id', true), '')::uuid)""")
    op.execute("""CREATE POLICY q_public_answer ON client_question FOR UPDATE USING (
        app_role() = 'PUBLIC_QUESTION' AND org_id = app_org()
        AND id = nullif(current_setting('app.question_id', true), '')::uuid)
        WITH CHECK (app_role() = 'PUBLIC_QUESTION' AND org_id = app_org())""")

    # ------------------------------------------------------------------ byrånivå
    for t in ("pipeline_step", "ai_usage", "rule_proposal"):
        enable(t)
        op.execute(f"CREATE POLICY o_all ON {t} FOR ALL USING (org_id = app_org()) WITH CHECK (org_id = app_org())")
    enable("ai_trace")
    op.execute(
        "CREATE POLICY o_read ON ai_trace FOR SELECT USING (org_id = app_org() AND "
        "(company_id IS NULL OR app_company_visible(company_id)))"
    )
    op.execute("CREATE POLICY o_insert ON ai_trace FOR INSERT WITH CHECK (org_id = app_org())")
    op.execute(
        "CREATE POLICY o_delete ON ai_trace FOR DELETE USING (org_id = app_org() AND app_role() IN ('ADMIN', 'WORKER'))"
    )
    enable("audit_event")
    op.execute(
        "CREATE POLICY a_read ON audit_event FOR SELECT USING (org_id = app_org() AND "
        "(app_role() = 'ADMIN' OR (company_id IS NOT NULL AND app_company_visible(company_id)) "
        "OR user_id = app_user_id()))"
    )
    op.execute(
        "CREATE POLICY a_insert ON audit_event FOR INSERT WITH CHECK (org_id = app_org() OR "
        "app_role() = 'PUBLIC_QUESTION')"
    )

    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}"
    )
    op.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE}")


def downgrade() -> None:
    raise NotImplementedError("Säkerhetsmigrationen rullas inte tillbaka automatiskt.")
