"""Kundminne: byråns tidigare bedömningar per kund och mönster.

När konsulten fattar ett beslut sparas det tillsammans med ett mönster (regel + konton +
motpart + beloppsnivå). Nästa gång samma mönster dyker upp föreslås samma beslut.
Minnet föreslår – det beslutar aldrig. High-fynd stängs aldrig automatiskt.
"""

from __future__ import annotations

import hashlib
import math
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from redovisningai.analytics.counterparties import guess_counterparty
from redovisningai.domain.ledger import Ledger
from redovisningai.findings.lifecycle import FindingRecord, FindingStatus


def amount_bucket(amount: Decimal | None) -> int | None:
    """Logaritmisk beloppsnivå – belopp inom ca ±20 % hamnar i samma eller närliggande hink."""
    if amount is None or amount == 0:
        return None
    return math.floor(math.log(float(abs(amount))) / math.log(1.5))


def _counterparty_key(rec: FindingRecord, ledger: Ledger | None) -> str:
    if ledger is None or not rec.vouchers:
        return ""
    wanted = set(rec.vouchers)
    for v in ledger.all_vouchers():
        if str(v.key) in wanted:
            g = guess_counterparty(v.text)
            if g.key:
                return g.key
    return ""


@dataclass(frozen=True, slots=True)
class PatternSignature:
    rule_code: str
    accounts: tuple[int, ...]
    counterparty: str
    bucket: int | None

    @property
    def exact(self) -> str:
        raw = f"{self.rule_code}|{','.join(map(str, self.accounts))}|{self.counterparty}|{self.bucket}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    @property
    def loose(self) -> str:
        raw = f"{self.rule_code}|{','.join(map(str, self.accounts))}|{self.counterparty}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def describe(self) -> str:
        parts = [self.rule_code]
        if self.accounts:
            parts.append("konto " + ", ".join(map(str, self.accounts)))
        if self.counterparty:
            parts.append(self.counterparty)
        return " · ".join(parts)


def signature_for(rec: FindingRecord, ledger: Ledger | None = None) -> PatternSignature:
    return PatternSignature(
        rule_code=rec.rule_code,
        accounts=tuple(sorted(set(rec.accounts)))[:4],
        counterparty=_counterparty_key(rec, ledger),
        bucket=amount_bucket(rec.amount),
    )


@dataclass(slots=True)
class Resolution:
    id: str
    company_id: str
    signature_exact: str
    signature_loose: str
    description: str
    rule_code: str
    decision: FindingStatus
    rationale: str
    decided_by: str
    decided_at: datetime
    valid_until: date | None = None
    times_reused: int = 0
    example_finding_id: str | None = None
    example_title: str | None = None

    def valid_on(self, d: date) -> bool:
        return self.valid_until is None or d <= self.valid_until

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "company_id": self.company_id,
            "description": self.description,
            "rule_code": self.rule_code,
            "decision": self.decision.value,
            "rationale": self.rationale,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at.isoformat(),
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
            "times_reused": self.times_reused,
            "example_title": self.example_title,
        }


REMEMBERED = {FindingStatus.ACCEPTED_OK, FindingStatus.RESOLVED, FindingStatus.ASK_CLIENT}


def remember(
    rec: FindingRecord,
    company_id: str,
    ledger: Ledger | None = None,
    *,
    valid_until: date | None = None,
) -> Resolution | None:
    if rec.status not in REMEMBERED or not rec.resolved_by or not rec.resolution_note:
        return None
    sig = signature_for(rec, ledger)
    return Resolution(
        id=str(uuid.uuid4()),
        company_id=company_id,
        signature_exact=sig.exact,
        signature_loose=sig.loose,
        description=sig.describe(),
        rule_code=rec.rule_code,
        decision=rec.status,
        rationale=rec.resolution_note,
        decided_by=rec.resolved_by,
        decided_at=rec.resolved_at or datetime.now(),
        valid_until=valid_until,
        example_finding_id=rec.id,
        example_title=rec.title,
    )


@dataclass(slots=True)
class MemorySuggestion:
    resolution_id: str
    decision: FindingStatus
    rationale: str
    decided_by: str
    decided_at: datetime
    confidence: str  # exact | similar
    text: str = field(default="")

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolution_id": self.resolution_id,
            "decision": self.decision.value,
            "rationale": self.rationale,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at.isoformat(),
            "confidence": self.confidence,
            "text": self.text,
        }


_DECISION_SV = {
    FindingStatus.ACCEPTED_OK: "bedömt OK",
    FindingStatus.RESOLVED: "åtgärdat",
    FindingStatus.ASK_CLIENT: "frågat kunden",
}


def suggest(
    rec: FindingRecord, resolutions: list[Resolution], ledger: Ledger | None = None, today: date | None = None
) -> MemorySuggestion | None:
    today = today or date.today()
    sig = signature_for(rec, ledger)
    valid = [r for r in resolutions if r.valid_on(today) and r.rule_code == rec.rule_code]
    exact = [r for r in valid if r.signature_exact == sig.exact]
    similar = [r for r in valid if r.signature_loose == sig.loose]
    pool, conf = (exact, "exact") if exact else (similar, "similar")
    if not pool:
        return None
    best = max(pool, key=lambda r: r.decided_at)
    months = [
        "januari",
        "februari",
        "mars",
        "april",
        "maj",
        "juni",
        "juli",
        "augusti",
        "september",
        "oktober",
        "november",
        "december",
    ]
    when = f"{months[best.decided_at.month - 1]} {best.decided_at.year}"
    prefix = "Samma mönster" if conf == "exact" else "Liknande mönster (annat belopp)"
    text = f"{prefix} som {when} – {_DECISION_SV.get(best.decision, best.decision.value)} av {best.decided_by}: {best.rationale}"
    return MemorySuggestion(best.id, best.decision, best.rationale, best.decided_by, best.decided_at, conf, text)


def apply_memory(
    records: list[FindingRecord],
    resolutions: list[Resolution],
    ledger: Ledger | None = None,
    today: date | None = None,
) -> int:
    """Sätt minnesförslag på öppna fynd. Returnerar antal fynd som fick förslag."""
    n = 0
    for rec in records:
        if not rec.status.is_open:
            continue
        s = suggest(rec, resolutions, ledger, today)
        rec.memory_suggestion = s.to_dict() if s else None
        n += 1 if s else 0
    return n
