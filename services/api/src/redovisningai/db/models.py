"""Databasmodell (PostgreSQL).

Alla kunddatatabeller har `org_id` och skyddas av Row Level Security (se migrationen).
Byrån (organization) är tenant; klientbolag (company) ligger under byrån.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

MONEY = Numeric(18, 2)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}  # noqa: RUF012


def _uuid() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _org() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"), nullable=False, index=True
    )


def _company() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("company.id", ondelete="CASCADE"), nullable=False, index=True)


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


# ---------------------------------------------------------------------------- tenant och användare


class Organization(Base):
    __tablename__ = "organization"
    id: Mapped[uuid.UUID] = _uuid()
    name: Mapped[str] = mapped_column(String(200))
    org_number: Mapped[str | None] = mapped_column(String(20))
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=sql_text("'{}'::jsonb"))
    encrypted_dek: Mapped[str | None] = mapped_column(Text)  # byråns datanyckel, krypterad med huvudnyckel
    ai_monthly_token_budget: Mapped[int] = mapped_column(BigInteger, default=5_000_000)
    created_at: Mapped[datetime] = _now()


class AppUser(Base):
    """Användare. Inte kunddata – samma person kan vara med i flera byråer."""

    __tablename__ = "app_user"
    id: Mapped[uuid.UUID] = _uuid()
    idp_subject: Mapped[str] = mapped_column(String(255), unique=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = _now()


class Membership(Base):
    __tablename__ = "organization_membership"
    __table_args__ = (UniqueConstraint("org_id", "user_id"),)
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))  # ADMIN | CONSULTANT | VIEWER
    can_payroll: Mapped[bool] = mapped_column(Boolean, default=False)
    can_aml: Mapped[bool] = mapped_column(Boolean, default=False)
    can_approve_reports: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _now()


class Company(Base):
    __tablename__ = "company"
    __table_args__ = (UniqueConstraint("org_id", "org_number"),)
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    name: Mapped[str] = mapped_column(String(200))
    org_number: Mapped[str | None] = mapped_column(String(20))
    legal_form: Mapped[str] = mapped_column(String(10), default="AB")
    industry: Mapped[str | None] = mapped_column(String(100))
    vat_period: Mapped[str] = mapped_column(String(10), default="quarter")
    food_retail: Mapped[bool] = mapped_column(Boolean, default=False)
    materiality: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("5000"))
    has_overdraft: Mapped[bool] = mapped_column(Boolean, default=False)
    accounting_method_override: Mapped[str | None] = mapped_column(String(10))
    source_system: Mapped[str | None] = mapped_column(String(30))  # fortnox | spiris | bl | sie_file …
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=sql_text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _now()
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CompanyAssignment(Base):
    __tablename__ = "company_assignment"
    org_id: Mapped[uuid.UUID] = _org()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("company.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True
    )


class Connection(Base):
    __tablename__ = "connection"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    company_id: Mapped[uuid.UUID] = _company()
    source: Mapped[str] = mapped_column(String(30))  # fortnox | spiris | sie_file | skatteverket
    status: Mapped[str] = mapped_column(String(20), default="PENDING")  # PENDING | OK | ERROR | REVOKED
    tenant_ref: Mapped[str | None] = mapped_column(String(100))
    secret_ref: Mapped[str | None] = mapped_column(Text)  # krypterad hemlighet eller referens till Key Vault
    authorized_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    cost_model: Mapped[str | None] = mapped_column(String(30))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=sql_text("'{}'::jsonb"))
    created_at: Mapped[datetime] = _now()


# ---------------------------------------------------------------------------- import och bokföring


class SourceFile(Base):
    __tablename__ = "source_file"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    company_id: Mapped[uuid.UUID] = _company()
    filename: Mapped[str] = mapped_column(String(300))
    object_key: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    detected_format: Mapped[str | None] = mapped_column(String(20))
    encoding: Mapped[str | None] = mapped_column(String(20))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    uploaded_at: Mapped[datetime] = _now()
    parse_issues: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default=sql_text("'[]'::jsonb"))
    delete_after: Mapped[date | None] = mapped_column(Date)


class FiscalYearRow(Base):
    __tablename__ = "fiscal_year"
    __table_args__ = (UniqueConstraint("company_id", "start_date"),)
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    company_id: Mapped[uuid.UUID] = _company()
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)


class ImportRun(Base):
    __tablename__ = "import_run"
    __table_args__ = (UniqueConstraint("company_id", "seq"),)
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    company_id: Mapped[uuid.UUID] = _company()
    fiscal_year_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("fiscal_year.id"))
    source_file_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("source_file.id"))
    source: Mapped[str] = mapped_column(String(30))
    seq: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")  # PENDING | COMPLETE | FAILED | SKIPPED
    parser_version: Mapped[str | None] = mapped_column(String(20))
    has_vouchers: Mapped[bool] = mapped_column(Boolean, default=True)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=sql_text("'{}'::jsonb"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _now()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class Account(Base):
    __tablename__ = "account"
    org_id: Mapped[uuid.UUID] = _org()
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("company.id", ondelete="CASCADE"), primary_key=True
    )
    number: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    type: Mapped[str | None] = mapped_column(String(1))
    sru: Mapped[str | None] = mapped_column(String(10))


class VoucherVersion(Base):
    """Versionerad verifikation. Giltig från import `valid_from` till (men inte med) `valid_to`."""

    __tablename__ = "voucher_version"
    __table_args__ = (
        Index(
            "ix_voucher_current",
            "company_id",
            "fiscal_year_id",
            "series",
            "number",
            postgresql_where=sql_text("valid_to IS NULL"),
        ),
        Index("ix_voucher_date", "company_id", "date"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = _org()
    company_id: Mapped[uuid.UUID] = _company()
    fiscal_year_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("fiscal_year.id"))
    series: Mapped[str] = mapped_column(String(20))
    number: Mapped[str] = mapped_column(String(40))
    date: Mapped[date] = mapped_column(Date)
    text: Mapped[str] = mapped_column(Text)
    reg_date: Mapped[date | None] = mapped_column(Date)
    signature: Mapped[str | None] = mapped_column(String(100))
    content_hash: Mapped[str] = mapped_column(String(64))
    valid_from: Mapped[int] = mapped_column(Integer)
    valid_to: Mapped[int | None] = mapped_column(Integer)
    source_line: Mapped[int | None] = mapped_column(Integer)


class TransactionRow(Base):
    __tablename__ = "transaction_row"
    __table_args__ = (Index("ix_row_account", "company_id", "account"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = _org()
    company_id: Mapped[uuid.UUID] = _company()
    voucher_version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("voucher_version.id", ondelete="CASCADE"), index=True
    )
    row_no: Mapped[int] = mapped_column(Integer)
    account: Mapped[int] = mapped_column(Integer)
    amount: Mapped[Decimal] = mapped_column(MONEY)
    trans_date: Mapped[date | None] = mapped_column(Date)
    text: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    objects: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default=sql_text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(String(10), default="normal")
    source_line: Mapped[int | None] = mapped_column(Integer)


class YearBalance(Base):
    __tablename__ = "year_balance"
    __table_args__ = (UniqueConstraint("import_id", "kind", "account"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = _org()
    company_id: Mapped[uuid.UUID] = _company()
    import_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("import_run.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(5))  # IB | UB | RES
    account: Mapped[int] = mapped_column(Integer)
    amount: Mapped[Decimal] = mapped_column(MONEY)


class PeriodAmount(Base):
    __tablename__ = "period_amount"
    __table_args__ = (UniqueConstraint("import_id", "kind", "period", "account"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = _org()
    company_id: Mapped[uuid.UUID] = _company()
    import_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("import_run.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(10))  # PSALDO | PBUDGET
    period: Mapped[date] = mapped_column(Date)
    account: Mapped[int] = mapped_column(Integer)
    amount: Mapped[Decimal] = mapped_column(MONEY)


class AccountPeriodBalance(Base):
    """Förberäknade månadsrörelser per import (aggregat – innehåller inga radnivådetaljer)."""

    __tablename__ = "account_period_balance"
    __table_args__ = (UniqueConstraint("import_id", "period", "account"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = _org()
    company_id: Mapped[uuid.UUID] = _company()
    import_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("import_run.id", ondelete="CASCADE"))
    period: Mapped[date] = mapped_column(Date)
    account: Mapped[int] = mapped_column(Integer)
    amount: Mapped[Decimal] = mapped_column(MONEY)


class PipelineStep(Base):
    """Idempotens: (import, steg, stegversion) körs högst en gång."""

    __tablename__ = "pipeline_step"
    __table_args__ = (UniqueConstraint("import_id", "step", "step_version"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = _org()
    import_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("import_run.id", ondelete="CASCADE"))
    step: Mapped[str] = mapped_column(String(50))
    step_version: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = _now()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------------- granskning


class FindingRow(Base):
    __tablename__ = "finding"
    __table_args__ = (UniqueConstraint("company_id", "fingerprint"),)
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    company_id: Mapped[uuid.UUID] = _company()
    fingerprint: Mapped[str] = mapped_column(String(64))
    rule_code: Mapped[str] = mapped_column(String(60), index=True)
    rule_version: Mapped[str] = mapped_column(String(10))
    severity: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    period: Mapped[str] = mapped_column(String(20))
    visibility: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), index=True)
    category: Mapped[str | None] = mapped_column(String(30))
    legal_basis: Mapped[str | None] = mapped_column(Text)
    vouchers: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    accounts: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    amount: Mapped[Decimal | None] = mapped_column(MONEY)
    facts: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    seen_in_reviews: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[str | None] = mapped_column(String(320))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suppression_id: Mapped[str | None] = mapped_column(String(40))
    memory_suggestion: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    case_key: Mapped[str | None] = mapped_column(String(40), index=True)


class SuppressionRuleRow(Base):
    __tablename__ = "suppression_rule"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("company.id", ondelete="CASCADE")
    )
    rule_code: Mapped[str] = mapped_column(String(60))
    reason: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(320))
    expires_at: Mapped[date | None] = mapped_column(Date)
    accounts: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    text_contains: Mapped[str | None] = mapped_column(Text)
    max_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    created_at: Mapped[datetime] = _now()


class ResolutionRow(Base):
    __tablename__ = "resolution"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    company_id: Mapped[uuid.UUID] = _company()
    signature_exact: Mapped[str] = mapped_column(String(40), index=True)
    signature_loose: Mapped[str] = mapped_column(String(40), index=True)
    description: Mapped[str] = mapped_column(Text)
    rule_code: Mapped[str] = mapped_column(String(60))
    decision: Mapped[str] = mapped_column(String(20))
    rationale: Mapped[str] = mapped_column(Text)
    decided_by: Mapped[str] = mapped_column(String(320))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[date | None] = mapped_column(Date)
    times_reused: Mapped[int] = mapped_column(Integer, default=0)
    example_finding_id: Mapped[str | None] = mapped_column(String(40))
    example_title: Mapped[str | None] = mapped_column(Text)


class CaseRow(Base):
    __tablename__ = "case_record"
    __table_args__ = (UniqueConstraint("company_id", "case_key"),)
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    company_id: Mapped[uuid.UUID] = _company()
    case_key: Mapped[str] = mapped_column(String(40))
    period: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(Text)
    root_cause: Mapped[str] = mapped_column(Text)
    suggested_action: Mapped[str] = mapped_column(Text)
    ask_client_suggested: Mapped[bool] = mapped_column(Boolean, default=False)
    severity: Mapped[str] = mapped_column(String(10))
    visibility: Mapped[str] = mapped_column(String(20))
    memory_hint: Mapped[str | None] = mapped_column(Text)
    ai: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = _now()


class PeriodReview(Base):
    __tablename__ = "period_review"
    __table_args__ = (UniqueConstraint("company_id", "period"),)
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    company_id: Mapped[uuid.UUID] = _company()
    period: Mapped[str] = mapped_column(String(7))  # ÅÅÅÅ-MM
    status: Mapped[str] = mapped_column(String(30), default="DATA_RECEIVED")
    maturity: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    approved_by: Mapped[str | None] = mapped_column(String(320))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    commentary: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    client_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    changes: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    updated_at: Mapped[datetime] = _now()


class ClientQuestion(Base):
    __tablename__ = "client_question"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    company_id: Mapped[uuid.UUID] = _company()
    case_key: Mapped[str | None] = mapped_column(String(40))
    finding_ids: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    text: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="SENT")  # SENT | ANSWERED | CLOSED | EXPIRED
    recipient_email: Mapped[str | None] = mapped_column(String(320))
    # Det kunden ser på svarssidan (bolagsnamn, byrånamn) – kopieras vid skapande eftersom den
    # publika sidan inte får läsa andra tabeller.
    display: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=sql_text("'{}'::jsonb"))
    sent_by: Mapped[str] = mapped_column(String(320))
    sent_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answer_text: Mapped[str | None] = mapped_column(Text)
    attachments: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    reminder_count: Mapped[int] = mapped_column(Integer, default=0)


class MappingOverride(Base):
    __tablename__ = "mapping_override"
    __table_args__ = (UniqueConstraint("org_id", "company_id", "kind", "account"),)
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("company.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(10))  # statement | category
    account: Mapped[int] = mapped_column(Integer)
    target: Mapped[str] = mapped_column(String(50))
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual | ai_confirmed
    created_by: Mapped[str] = mapped_column(String(320))
    created_at: Mapped[datetime] = _now()


class RuleParamOverride(Base):
    __tablename__ = "rule_param_override"
    __table_args__ = (UniqueConstraint("org_id", "company_id", "rule_code"),)
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("company.id", ondelete="CASCADE")
    )
    rule_code: Mapped[str] = mapped_column(String(60))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_by: Mapped[str] = mapped_column(String(320))
    created_at: Mapped[datetime] = _now()


class CounterpartyAlias(Base):
    __tablename__ = "counterparty_alias"
    __table_args__ = (UniqueConstraint("org_id", "company_id", "key"),)
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("company.id", ondelete="CASCADE")
    )
    key: Mapped[str] = mapped_column(String(200))
    canonical_name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(50))
    confidence: Mapped[Decimal] = mapped_column(Numeric(4, 3), default=Decimal("1"))
    confirmed_by: Mapped[str | None] = mapped_column(String(320))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AmlAssessment(Base):
    __tablename__ = "aml_assessment"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    company_id: Mapped[uuid.UUID] = _company()
    risk_level: Mapped[str] = mapped_column(String(10))  # LOW | NORMAL | HIGH
    factors: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str] = mapped_column(String(320))
    decided_at: Mapped[datetime] = _now()


class TaxAccountImport(Base):
    __tablename__ = "tax_account_import"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    company_id: Mapped[uuid.UUID] = _company()
    source: Mapped[str] = mapped_column(String(20))  # file | api
    transactions: Mapped[list[Any]] = mapped_column(JSONB)
    opening_balance: Mapped[Decimal] = mapped_column(MONEY, default=Decimal(0))
    imported_by: Mapped[str] = mapped_column(String(320))
    imported_at: Mapped[datetime] = _now()


class RuleProposal(Base):
    __tablename__ = "rule_proposal"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    source_url: Mapped[str] = mapped_column(Text)
    proposals: Mapped[list[Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(30), default="PENDING_EXPERT_REVIEW")
    reviewed_by: Mapped[str | None] = mapped_column(String(320))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


# ---------------------------------------------------------------------------- spårbarhet


class AuditEvent(Base):
    """Revisionslogg. Endast INSERT och SELECT är tillåtna för applikationsrollen."""

    __tablename__ = "audit_event"
    # Ingen RETURNING vid insert: kontexter som får skriva men inte läsa loggen ska fungera.
    __table_args__ = {"implicit_returning": False}  # noqa: RUF012
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID] = _org()
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    user_email: Mapped[str | None] = mapped_column(String(320))
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(80), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    at: Mapped[datetime] = _now()


class AITraceRow(Base):
    __tablename__ = "ai_trace"
    id: Mapped[uuid.UUID] = _uuid()
    org_id: Mapped[uuid.UUID] = _org()
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    task: Mapped[str] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(10))
    provider: Mapped[str | None] = mapped_column(String(50))
    model: Mapped[str | None] = mapped_column(String(100))
    region: Mapped[str | None] = mapped_column(String(50))
    input_hash: Mapped[str] = mapped_column(String(32))
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    rejected: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    downgraded: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    package: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    tool_calls: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AIUsage(Base):
    __tablename__ = "ai_usage"
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organization.id", ondelete="CASCADE"), primary_key=True
    )
    month: Mapped[str] = mapped_column(String(7), primary_key=True)
    tokens: Mapped[int] = mapped_column(BigInteger, default=0)


# Tabeller med company_id som skyddas per klienttilldelning i RLS.
COMPANY_TABLES = [
    "company_assignment",
    "connection",
    "source_file",
    "fiscal_year",
    "import_run",
    "account",
    "voucher_version",
    "transaction_row",
    "year_balance",
    "period_amount",
    "account_period_balance",
    "finding",
    "resolution",
    "case_record",
    "period_review",
    "client_question",
    "aml_assessment",
    "tax_account_import",
]
# Tabeller med valfritt company_id (NULL = gäller hela byrån).
OPTIONAL_COMPANY_TABLES = ["suppression_rule", "mapping_override", "rule_param_override", "counterparty_alias"]
ORG_TABLES = ["organization_membership", "pipeline_step", "audit_event", "ai_trace", "ai_usage", "rule_proposal"]
