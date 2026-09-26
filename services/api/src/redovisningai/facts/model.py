"""Fakta: varje siffra som visas för en användare eller skickas till AI är ett Fact.

Ett Fact har ett stabilt id, ett exakt värde, en enhet, en status och en härkomst
(lineage: konton, period, källimport, beräkningsversion). AI-texter refererar till fakta
via `{f:<id>}` och servern renderar värdet – AI:n skriver aldrig siffror själv.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

CALC_VERSION = "2026.09.1"


class Unit(StrEnum):
    SEK = "SEK"
    PERCENT = "percent"  # värde i procent, t.ex. 12.5
    PP = "pp"  # procentenheter
    COUNT = "count"
    RATIO = "ratio"  # t.ex. kassalikviditet 1.4
    DAYS = "days"
    TEXT = "text"


class FactStatus(StrEnum):
    CALCULATED = "CALCULATED"
    PARTIAL = "PARTIAL"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    ERROR = "ERROR"


class Visibility(StrEnum):
    INTERNAL = "INTERNAL"
    CLIENT_SAFE = "CLIENT_SAFE"
    RESTRICTED_AML = "RESTRICTED_AML"


@dataclass(frozen=True, slots=True)
class Fact:
    id: str
    kind: str  # metric | amount | variance_component | balance | count | text
    subject: str  # t.ex. "metric:operating_margin", "line:personnel", "account:6540"
    label: str
    value: Decimal | None
    unit: Unit
    period: str | None = None
    compare_period: str | None = None
    status: FactStatus = FactStatus.CALCULATED
    lineage: dict[str, Any] = field(default_factory=dict)
    visibility: Visibility = Visibility.CLIENT_SAFE
    text_value: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "subject": self.subject,
            "label": self.label,
            "value": None if self.value is None else str(self.value),
            "unit": self.unit.value,
            "period": self.period,
            "compare_period": self.compare_period,
            "status": self.status.value,
            "lineage": self.lineage,
            "visibility": self.visibility.value,
            "text_value": self.text_value,
            "display": render_value(self),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Fact:
        """Återskapa ett Fact från to_dict() (t.ex. fakta som sparats med ett fynd)."""
        value = data.get("value")
        return cls(
            id=str(data["id"]),
            kind=str(data.get("kind", "")),
            subject=str(data.get("subject", "")),
            label=str(data.get("label", "")),
            value=None if value is None else Decimal(str(value)),
            unit=Unit(data.get("unit", Unit.SEK.value)),
            period=data.get("period"),
            compare_period=data.get("compare_period"),
            status=FactStatus(data.get("status", FactStatus.CALCULATED.value)),
            lineage=dict(data.get("lineage") or {}),
            # Okänd synlighet behandlas försiktigt: inte kundsäker.
            visibility=Visibility(data.get("visibility", Visibility.INTERNAL.value)),
            text_value=data.get("text_value"),
        )


def make_fact_id(kind: str, subject: str, period: str | None, compare: str | None = None, extra: str = "") -> str:
    raw = json.dumps([kind, subject, period, compare, extra, CALC_VERSION])
    digest = hashlib.sha256(raw.encode()).hexdigest()[:10]
    slug = re.sub(r"[^a-z0-9]+", "_", subject.lower()).strip("_")[:28]
    return f"{slug}_{digest}"


class FactStore:
    """Samling fakta för en analys (t.ex. en månadsgranskning eller ett AI-paket)."""

    def __init__(self) -> None:
        self._facts: dict[str, Fact] = {}

    def add(self, fact: Fact) -> Fact:
        self._facts[fact.id] = fact
        return fact

    def new(
        self,
        kind: str,
        subject: str,
        label: str,
        value: Decimal | None,
        unit: Unit,
        *,
        period: str | None = None,
        compare_period: str | None = None,
        status: FactStatus = FactStatus.CALCULATED,
        lineage: dict[str, Any] | None = None,
        visibility: Visibility = Visibility.CLIENT_SAFE,
        text_value: str | None = None,
        extra: str = "",
    ) -> Fact:
        fid = make_fact_id(kind, subject, period, compare_period, extra)
        return self.add(
            Fact(
                id=fid,
                kind=kind,
                subject=subject,
                label=label,
                value=value,
                unit=unit,
                period=period,
                compare_period=compare_period,
                status=status,
                lineage={**(lineage or {}), "calc_version": CALC_VERSION},
                visibility=visibility,
                text_value=text_value,
            )
        )

    def get(self, fid: str) -> Fact | None:
        return self._facts.get(fid)

    def __contains__(self, fid: object) -> bool:
        return fid in self._facts

    def __iter__(self) -> Iterator[Fact]:
        return iter(self._facts.values())

    def __len__(self) -> int:
        return len(self._facts)

    def extend(self, facts: Iterable[Fact]) -> None:
        for f in facts:
            self.add(f)

    def subset(self, ids: Iterable[str]) -> FactStore:
        s = FactStore()
        for i in ids:
            f = self._facts.get(i)
            if f is not None:
                s.add(f)
        return s

    def to_list(self) -> list[dict[str, Any]]:
        return [f.to_dict() for f in self._facts.values()]


# --------------------------------------------------------------------------- rendering (sv-SE)

MINUS = "−"
NBSP = " "


def _group(n: Decimal, decimals: int) -> str:
    q = Decimal(1).scaleb(-decimals) if decimals else Decimal(1)
    v = n.quantize(q, rounding=ROUND_HALF_UP)
    sign = MINUS if v < 0 else ""
    v = abs(v)
    int_part, _, frac = f"{v:.{decimals}f}".partition(".")
    groups: list[str] = []
    while len(int_part) > 3:
        groups.insert(0, int_part[-3:])
        int_part = int_part[:-3]
    groups.insert(0, int_part)
    out = NBSP.join(groups)
    if decimals:
        out += "," + frac
    return sign + out


def format_sek(amount: Decimal, *, signed: bool = False) -> str:
    """1 240 000 → '1,24 Mkr', 412 300 → '412 tkr', 8 450 → '8 450 kr'."""
    a = abs(amount)
    if a >= Decimal(1_000_000):
        body = _group(amount / Decimal(1_000_000), 2) + f"{NBSP}Mkr"
    elif a >= Decimal(10_000):
        body = _group(amount / Decimal(1000), 0) + f"{NBSP}tkr"
    else:
        body = _group(amount, 0) + f"{NBSP}kr"
    if signed and amount > 0:
        body = "+" + body
    return body


def format_percent(value: Decimal, *, signed: bool = False, decimals: int = 1) -> str:
    body = _group(value, decimals) + f"{NBSP}%"
    if signed and value > 0:
        body = "+" + body
    return body


def render_value(f: Fact) -> str:
    if f.status in (FactStatus.INSUFFICIENT_DATA, FactStatus.NOT_APPLICABLE, FactStatus.ERROR) or (
        f.value is None and f.text_value is None
    ):
        return {
            FactStatus.INSUFFICIENT_DATA: "otillräckligt underlag",
            FactStatus.NOT_APPLICABLE: "ej tillämpligt",
            FactStatus.ERROR: "beräkningsfel",
        }.get(f.status, "–")
    if f.unit is Unit.TEXT:
        return f.text_value or ""
    assert f.value is not None
    signed = f.kind in ("variance_component", "change")
    match f.unit:
        case Unit.SEK:
            return format_sek(f.value, signed=signed)
        case Unit.PERCENT:
            return format_percent(f.value, signed=signed)
        case Unit.PP:
            body = _group(f.value, 1) + f"{NBSP}procentenheter"
            return ("+" + body) if f.value > 0 else body
        case Unit.COUNT:
            return _group(f.value, 0)
        case Unit.RATIO:
            return _group(f.value, 2)
        case Unit.DAYS:
            return _group(f.value, 0) + f"{NBSP}dagar"
    return str(f.value)


PLACEHOLDER_RE = re.compile(r"\{f:([^}\s]+)\}")


def render_text(text: str, store: FactStore) -> str:
    """Ersätt {f:id} med renderat värde. Okända id lämnas som '[saknas]'."""

    def sub(m: re.Match[str]) -> str:
        f = store.get(m.group(1))
        return render_value(f) if f is not None else "[saknas]"

    return PLACEHOLDER_RE.sub(sub, text)


def pct_change(current: Decimal, previous: Decimal) -> Decimal | None:
    if previous == 0:
        return None
    return ((current - previous) / abs(previous) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
