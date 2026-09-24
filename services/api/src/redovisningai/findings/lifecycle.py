"""Fyndens livscykel, undertryckning och precision per regel.

Ett fynd identifieras av sitt fingeravtryck. Samma problem ger samma fynd natt efter natt –
konsultens beslut ligger kvar. Fynd som försvinner ur bokföringen (t.ex. för att kunden
rättat) stängs automatiskt, men bara när samma granskningsperiod körs om.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from redovisningai.facts.model import Visibility
from redovisningai.rules.engine import FindingCandidate, Severity


class FindingStatus(StrEnum):
    NEW = "NEW"
    IN_PROGRESS = "IN_PROGRESS"
    ASK_CLIENT = "ASK_CLIENT"
    RESOLVED = "RESOLVED"  # åtgärdat av konsult
    ACCEPTED_OK = "ACCEPTED_OK"  # bedömt OK, ingen åtgärd
    SUPPRESSED = "SUPPRESSED"  # undertryckt av regel
    AUTO_CLOSED = "AUTO_CLOSED"  # försvann ur bokföringen

    @property
    def is_open(self) -> bool:
        return self in (FindingStatus.NEW, FindingStatus.IN_PROGRESS, FindingStatus.ASK_CLIENT)


ACTIONED = {FindingStatus.RESOLVED, FindingStatus.ASK_CLIENT}
NOT_ACTIONED = {FindingStatus.ACCEPTED_OK, FindingStatus.SUPPRESSED}


@dataclass(slots=True)
class FindingRecord:
    id: str
    fingerprint: str
    rule_code: str
    rule_version: str
    severity: Severity
    title: str
    description: str
    period: str
    visibility: Visibility
    status: FindingStatus = FindingStatus.NEW
    vouchers: list[str] = field(default_factory=list)
    accounts: list[int] = field(default_factory=list)
    amount: Decimal | None = None
    facts: list[dict[str, Any]] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    legal_basis: str = ""
    category: str = ""
    seen_in_reviews: set[str] = field(default_factory=set)
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    resolution_note: str | None = None
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    suppression_id: str | None = None
    memory_suggestion: dict[str, Any] | None = None
    case_key: str | None = None

    @staticmethod
    def from_candidate(c: FindingCandidate, review: str, now: datetime) -> FindingRecord:
        return FindingRecord(
            id=str(uuid.uuid4()),
            fingerprint=c.fingerprint,
            rule_code=c.rule_code,
            rule_version=c.rule_version,
            severity=c.severity,
            title=c.title,
            description=c.description,
            period=c.period,
            visibility=c.visibility,
            vouchers=list(c.vouchers),
            accounts=list(c.accounts),
            amount=c.amount,
            facts=[f.to_dict() for f in c.facts],
            details=dict(c.details),
            legal_basis=c.legal_basis,
            category=c.category,
            seen_in_reviews={review},
            first_seen=now,
            last_seen=now,
        )

    def refresh(self, c: FindingCandidate, review: str, now: datetime) -> None:
        self.rule_version = c.rule_version
        self.severity = c.severity
        self.title = c.title
        self.description = c.description
        self.vouchers = list(c.vouchers)
        self.accounts = list(c.accounts)
        self.amount = c.amount
        self.facts = [f.to_dict() for f in c.facts]
        self.details = dict(c.details)
        self.seen_in_reviews.add(review)
        self.last_seen = now

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "fingerprint": self.fingerprint,
            "rule_code": self.rule_code,
            "rule_version": self.rule_version,
            "severity": self.severity.value,
            "title": self.title,
            "description": self.description,
            "period": self.period,
            "visibility": self.visibility.value,
            "status": self.status.value,
            "vouchers": self.vouchers,
            "accounts": self.accounts,
            "amount": None if self.amount is None else str(self.amount),
            "facts": self.facts,
            "details": self.details,
            "legal_basis": self.legal_basis,
            "category": self.category,
            "seen_in_reviews": sorted(self.seen_in_reviews),
            "resolution_note": self.resolution_note,
            "resolved_by": self.resolved_by,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "memory_suggestion": self.memory_suggestion,
            "case_key": self.case_key,
        }


@dataclass(slots=True)
class SuppressionRule:
    id: str
    rule_code: str
    reason: str
    created_by: str
    expires_at: date | None = None
    company_id: str | None = None  # None = hela byrån
    accounts: list[int] = field(default_factory=list)
    text_contains: str | None = None
    max_amount: Decimal | None = None

    def matches(self, c: FindingCandidate, today: date) -> bool:
        if self.expires_at is not None and today > self.expires_at:
            return False
        if c.rule_code != self.rule_code:
            return False
        if c.visibility is Visibility.RESTRICTED_AML:
            return False  # PTL-signaler kan inte undertryckas generellt
        if self.accounts and not set(self.accounts) & set(c.accounts):
            return False
        if self.text_contains and not re.search(re.escape(self.text_contains), c.title + " " + c.description, re.I):
            return False
        return not (self.max_amount is not None and c.amount is not None and abs(c.amount) > self.max_amount)


@dataclass(slots=True)
class ReconcileResult:
    created: list[FindingRecord]
    updated: list[FindingRecord]
    auto_closed: list[FindingRecord]
    reopened: list[FindingRecord]


def reconcile(
    existing: list[FindingRecord],
    candidates: list[FindingCandidate],
    review: str,
    *,
    suppressions: list[SuppressionRule] | None = None,
    now: datetime | None = None,
) -> ReconcileResult:
    """Uppdatera fynd för en granskningsperiod med resultatet av en ny körning."""
    now = now or datetime.now()
    today = now.date()
    by_fp = {r.fingerprint: r for r in existing}
    produced: set[str] = set()
    res = ReconcileResult([], [], [], [])
    for c in candidates:
        produced.add(c.fingerprint)
        rec = by_fp.get(c.fingerprint)
        if rec is None:
            rec = FindingRecord.from_candidate(c, review, now)
            for s in suppressions or []:
                if s.matches(c, today):
                    rec.status = FindingStatus.SUPPRESSED
                    rec.suppression_id = s.id
                    rec.resolution_note = f"Undertryckt: {s.reason}"
                    break
            by_fp[c.fingerprint] = rec
            res.created.append(rec)
            continue
        was_closed = rec.status is FindingStatus.AUTO_CLOSED
        rec.refresh(c, review, now)
        if was_closed:
            rec.status = FindingStatus.NEW
            rec.resolution_note = None
            res.reopened.append(rec)
        res.updated.append(rec)
    for rec in existing:
        if rec.fingerprint in produced or review not in rec.seen_in_reviews:
            continue
        rec.seen_in_reviews.discard(review)
        if not rec.seen_in_reviews and rec.status.is_open:
            rec.status = FindingStatus.AUTO_CLOSED
            rec.resolution_note = "Försvann ur bokföringen (troligen rättat)."
            rec.resolved_at = now
            res.auto_closed.append(rec)
    return res


def decide(
    rec: FindingRecord, status: FindingStatus, user: str, note: str | None = None, now: datetime | None = None
) -> None:
    if status in (FindingStatus.AUTO_CLOSED, FindingStatus.SUPPRESSED):
        raise ValueError("Status sätts av systemet")
    if status in (FindingStatus.ACCEPTED_OK,) and not (note and note.strip()):
        raise ValueError("Motivering krävs när ett fynd bedöms OK")
    rec.status = status
    rec.resolution_note = note
    rec.resolved_by = user
    rec.resolved_at = now or datetime.now()


@dataclass(slots=True)
class RulePrecision:
    rule_code: str
    total: int
    actioned: int
    not_actioned: int
    open: int

    @property
    def precision(self) -> float | None:
        decided = self.actioned + self.not_actioned
        return None if decided == 0 else self.actioned / decided

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_code": self.rule_code,
            "total": self.total,
            "actioned": self.actioned,
            "not_actioned": self.not_actioned,
            "open": self.open,
            "precision": self.precision,
        }


def precision_by_rule(records: list[FindingRecord]) -> list[RulePrecision]:
    agg: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for r in records:
        a = agg[r.rule_code]
        a[0] += 1
        if r.status in ACTIONED:
            a[1] += 1
        elif r.status in NOT_ACTIONED:
            a[2] += 1
        elif r.status.is_open:
            a[3] += 1
    return sorted((RulePrecision(k, *v) for k, v in agg.items()), key=lambda p: p.rule_code)
