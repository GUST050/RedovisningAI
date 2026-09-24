"""Ärenden: grupperar relaterade fynd till ett beslut med en trolig orsak.

Deterministisk grund (fungerar utan AI): fynd kopplas ihop om de delar verifikation,
eller rör samma konto i samma period och hör till samma orsaksfamilj. AI (A2) kan sedan
förfina titel, orsakshypotes och gruppering – men grunden är alltid spårbar.

PTL-signaler (RESTRICTED_AML) grupperas aldrig ihop med andra fynd.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from redovisningai.facts.model import Visibility
from redovisningai.findings.lifecycle import FindingRecord, FindingStatus
from redovisningai.rules.engine import Severity

# Orsaksfamiljer: fynd i samma familj och samma period/konto hör troligen ihop.
FAMILIES: dict[str, set[str]] = {
    "duplicate": {"DUPLICATE_CANDIDATE", "COST_DEVIATION", "INPUT_VAT_RATIO", "UNUSUAL_ACCOUNT_COMBINATION"},
    "vat": {"VAT_ACCOUNTS_NOT_CLEARED", "INPUT_VAT_RATIO", "OUTPUT_VAT_RATIO", "TAX_ACCOUNT_BALANCE"},
    "payroll": {
        "PAYROLL_TAX_NOT_CLEARED",
        "EMPLOYER_CONTRIBUTION_RATIO",
        "TAX_ACCOUNT_BALANCE",
        "VACATION_LIABILITY_STATIC",
    },
    "related": {"RELATED_PARTY_RECEIVABLE", "LARGE_MANUAL_POSTING", "UNUSUAL_ACCOUNT_COMBINATION"},
    "cash": {"ABNORMAL_SIGN", "LATE_BOOKING", "NEGATIVE_BANK"},
    "integrity": {"UNBALANCED_VOUCHER", "VOUCHER_NUMBER_GAP", "IB_NE_PREV_UB", "ACCOUNT_NOT_IN_CHART"},
    "reversal": {"RAPID_REVERSAL", "UNUSUAL_ACCOUNT_COMBINATION", "LARGE_MANUAL_POSTING"},
    "closing": {"ASSETS_WITHOUT_DEPRECIATION", "VACATION_LIABILITY_STATIC", "TAX_ALLOCATION_RESERVE_DUE"},
}


@dataclass(frozen=True, slots=True)
class CaseTemplate:
    title: str
    root_cause: str
    action: str
    ask_client: bool


TEMPLATES: dict[str, CaseTemplate] = {
    "DUPLICATE_CANDIDATE": CaseTemplate(
        "Trolig dubbelbokning",
        "Samma faktura eller betalning verkar vara bokförd två gånger. Det kan också "
        "förklara högre kostnad och ingående moms för perioden.",
        "Utred och makulera dubbletten om den är felaktig.",
        False,
    ),
    "RELATED_PARTY_RECEIVABLE": CaseTemplate(
        "Fordran på delägare – kontrollera låneförbudet",
        "Bolaget har en fordran på delägare eller närstående, vilket kan vara ett förbjudet lån enligt ABL 21 kap.",
        "Fråga kunden om syftet och hur beloppet ska regleras.",
        True,
    ),
    "VAT_ACCOUNTS_NOT_CLEARED": CaseTemplate(
        "Momsredovisningen behöver stämmas av",
        "Momskonton har saldo efter momsperioden – ett konto kan saknas i "
        "momsredovisningen eller en rättelse har bokats efteråt.",
        "Stäm av momsdeklarationen mot bokföringen.",
        False,
    ),
    "OUTPUT_VAT_RATIO": CaseTemplate(
        "Fel momssats på försäljningen",
        "Försäljningen har en momssats som inte stämmer för datumet eller varan.",
        "Kontrollera kassasystemets momsinställning och rätta via momsdeklarationen.",
        True,
    ),
    "INPUT_VAT_RATIO": CaseTemplate(
        "Ingående moms stämmer inte",
        "Avdragen moms motsvarar ingen giltig sats eller gäller en momsfri kostnad.",
        "Kontrollera fakturan och rätta momsavdraget.",
        False,
    ),
    "EMPLOYER_CONTRIBUTION_RATIO": CaseTemplate(
        "Arbetsgivaravgifter behöver stämmas av",
        "Arbetsgivaravgifterna stämmer inte med lönerna för perioden.",
        "Stäm av mot arbetsgivardeklarationen (AGI).",
        False,
    ),
    "PAYROLL_TAX_NOT_CLEARED": CaseTemplate(
        "Löneskatter nollas inte",
        "Skatter och avgifter från tidigare månader ligger kvar som skuld.",
        "Stäm av skattekontot och dragningar.",
        False,
    ),
    "UNBALANCED_VOUCHER": CaseTemplate(
        "Obalanserad verifikation",
        "En verifikation balanserar inte – troligen en felregistrering eller ofullständig import.",
        "Rätta verifikationen i bokföringssystemet.",
        False,
    ),
    "CHANGED_AFTER_APPROVAL": CaseTemplate(
        "Bokföringen ändrades efter godkännande",
        "Verifikationer har lagts till, ändrats eller tagits bort i en redan godkänd period.",
        "Granska ändringarna och godkänn perioden på nytt.",
        False,
    ),
    "ASSETS_WITHOUT_DEPRECIATION": CaseTemplate(
        "Avskrivningar saknas",
        "Inventarier finns men avskrivningar har inte bokats.",
        "Boka avskrivningar enligt avskrivningsplanen.",
        False,
    ),
    "LARGE_MANUAL_POSTING": CaseTemplate(
        "Stor manuell post nära periodslut",
        "En stor manuell verifikation nära periodens slut behöver underlag.",
        "Kontrollera underlaget för posten.",
        True,
    ),
    "RAPID_REVERSAL": CaseTemplate(
        "Bokning som återförts direkt", "En bokning återfördes inom några dagar.", "Kontrollera syftet.", False
    ),
    "ABNORMAL_SIGN": CaseTemplate(
        "Onormalt saldo",
        "Ett konto har ett saldo som inte är rimligt, t.ex. negativ kassa.",
        "Stäm av kontot och boka om felaktiga poster.",
        False,
    ),
    "LATE_BOOKING": CaseTemplate(
        "Sent bokförd affärshändelse",
        "Affärshändelsen bokfördes senare än bokföringslagen kräver.",
        "Påminn kunden om rutinen för löpande bokföring.",
        True,
    ),
}

ACTION_ORDER = [
    "DUPLICATE_CANDIDATE",
    "CHANGED_AFTER_APPROVAL",
    "UNBALANCED_VOUCHER",
    "RELATED_PARTY_RECEIVABLE",
    "OUTPUT_VAT_RATIO",
    "VAT_ACCOUNTS_NOT_CLEARED",
    "EMPLOYER_CONTRIBUTION_RATIO",
    "PAYROLL_TAX_NOT_CLEARED",
    "INPUT_VAT_RATIO",
    "LARGE_MANUAL_POSTING",
    "RAPID_REVERSAL",
    "ASSETS_WITHOUT_DEPRECIATION",
    "ABNORMAL_SIGN",
    "LATE_BOOKING",
]


@dataclass(slots=True)
class Case:
    key: str
    title: str
    root_cause: str
    suggested_action: str
    ask_client_suggested: bool
    severity: Severity
    visibility: Visibility
    period: str
    findings: list[FindingRecord]
    memory_hint: str | None = None
    source: str = "rules"  # rules | ai
    claims: list[dict[str, Any]] = field(default_factory=list)

    @property
    def status(self) -> str:
        statuses = {f.status for f in self.findings}
        if statuses & {FindingStatus.NEW}:
            return "OPEN"
        if statuses & {FindingStatus.IN_PROGRESS}:
            return "IN_PROGRESS"
        if statuses & {FindingStatus.ASK_CLIENT}:
            return "WAITING_CLIENT"
        return "CLOSED"

    @property
    def vouchers(self) -> list[str]:
        return sorted({v for f in self.findings for v in f.vouchers})

    @property
    def accounts(self) -> list[int]:
        return sorted({a for f in self.findings for a in f.accounts})

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "root_cause": self.root_cause,
            "suggested_action": self.suggested_action,
            "ask_client_suggested": self.ask_client_suggested,
            "severity": self.severity.value,
            "visibility": self.visibility.value,
            "period": self.period,
            "status": self.status,
            "memory_hint": self.memory_hint,
            "source": self.source,
            "claims": self.claims,
            "vouchers": self.vouchers,
            "accounts": self.accounts,
            "finding_ids": [f.id for f in self.findings],
            "findings": [f.to_dict() for f in self.findings],
        }


def _related(a: FindingRecord, b: FindingRecord) -> bool:
    if (a.visibility is Visibility.RESTRICTED_AML) != (b.visibility is Visibility.RESTRICTED_AML):
        return False
    if a.visibility is Visibility.RESTRICTED_AML:
        return bool(set(a.vouchers) & set(b.vouchers))
    if set(a.vouchers) & set(b.vouchers):
        return True
    same_family = any(a.rule_code in fam and b.rule_code in fam for fam in FAMILIES.values())
    if not same_family or a.period != b.period:
        return False
    if a.rule_code == b.rule_code:
        return False  # samma regel i samma period = separata problem om de inte delar verifikation
    if set(a.accounts) & set(b.accounts):
        return True
    # Moms- och lönefamiljerna hör ihop per period även utan gemensamt konto.
    return any(a.rule_code in FAMILIES[f] and b.rule_code in FAMILIES[f] for f in ("vat", "payroll"))


def _components(records: list[FindingRecord]) -> list[list[FindingRecord]]:
    parent = list(range(len(records)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            if _related(records[i], records[j]):
                parent[find(i)] = find(j)
    groups: dict[int, list[FindingRecord]] = defaultdict(list)
    for i, r in enumerate(records):
        groups[find(i)].append(r)
    return list(groups.values())


def _lead(findings: list[FindingRecord]) -> FindingRecord:
    def rank(f: FindingRecord) -> tuple[int, int]:
        order = ACTION_ORDER.index(f.rule_code) if f.rule_code in ACTION_ORDER else len(ACTION_ORDER)
        return (-f.severity.rank, order)

    return min(findings, key=rank)


def build_cases(records: list[FindingRecord], *, include_closed: bool = False) -> list[Case]:
    pool = [r for r in records if include_closed or r.status.is_open]
    cases: list[Case] = []
    for group in _components(pool):
        lead = _lead(group)
        tpl = TEMPLATES.get(lead.rule_code)
        if len(group) == 1 or tpl is None:
            title = lead.title
        else:
            title = f"{tpl.title}: {lead.title}" if tpl.title not in lead.title else lead.title
        root = tpl.root_cause if tpl else lead.description
        if len(group) > 1:
            root += " Hör ihop med: " + "; ".join(f.title for f in group if f is not lead) + "."
        severity = max((f.severity for f in group), key=lambda s: s.rank)
        visibility = (
            Visibility.RESTRICTED_AML
            if any(f.visibility is Visibility.RESTRICTED_AML for f in group)
            else (
                Visibility.INTERNAL
                if any(f.visibility is Visibility.INTERNAL for f in group)
                else Visibility.CLIENT_SAFE
            )
        )
        key = hashlib.sha256("|".join(sorted(f.fingerprint for f in group)).encode()).hexdigest()[:24]
        hints = [f.memory_suggestion for f in group if f.memory_suggestion]
        memory_hint = None
        if hints and len(hints) == len(group):
            memory_hint = hints[0].get("text")
        elif hints:
            memory_hint = f"{len(hints)} av {len(group)} fynd har tidigare bedömningar."
        ask = bool(tpl and tpl.ask_client) and visibility is not Visibility.RESTRICTED_AML
        for f in group:
            f.case_key = key
        cases.append(
            Case(
                key=key,
                title=title,
                root_cause=root,
                suggested_action=tpl.action if tpl else "Utred fyndet.",
                ask_client_suggested=ask,
                severity=severity,
                visibility=visibility,
                period=lead.period,
                findings=sorted(group, key=lambda f: (-f.severity.rank, f.rule_code)),
                memory_hint=memory_hint,
            )
        )
    cases.sort(key=lambda c: (-c.severity.rank, c.period, c.title))
    return cases
