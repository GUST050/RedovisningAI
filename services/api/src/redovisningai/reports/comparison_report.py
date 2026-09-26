"""Jämförelserapport: de skillnader och jämförelser som konsulten valt, med underlag.

Rapporten byggs bara av valda poster från `analytics.differences` och av beräknade fakta. Varje
post får sin deterministiska sammanfattning, konsultens kommentar och en tabell med underlaget
(brygga, konton eller utveckling över tid). Kundrapporten innehåller aldrig analysfynd,
verifikationer eller enskilda lönekonton.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from redovisningai.accounting.balances import AccountSet, LedgerIndex
from redovisningai.accounting.comparisons import ComparisonPair
from redovisningai.accounting.metrics import REGISTRY
from redovisningai.accounting.statements import StatementMapping
from redovisningai.accounting.structure import SERIES_KINDS, MetricStructure, metric_structure, period_series
from redovisningai.analytics.differences import DifferenceItem, DifferenceSet, fmt_change, fmt_value
from redovisningai.domain.ledger import ZERO
from redovisningai.facts.model import CALC_VERSION, FactStatus, format_percent, format_sek
from redovisningai.reports.document import Document, Section, Table
from redovisningai.rules.rates import RateTable

MAX_ITEMS = 30
MAX_COMMENT = 2000
MAX_INTRO = 4000
MAX_TITLE = 200
TOP_ROWS = 8
TOP_VOUCHERS = 5
AUDIENCES = ("internal", "client")
FORMATS = ("pdf", "docx", "xlsx")


class ReportSelectionError(ValueError):
    """Urvalet går inte att göra en rapport av (okänd post, fel mottagare, för många poster …)."""


@dataclass(frozen=True, slots=True)
class SelectedItem:
    id: str
    comment: str | None = None
    series: str | None = None  # för "structure:<nyckeltal>"
    count: int | None = None


@dataclass(slots=True)
class _Grid:
    """Tabell med typade kolumner: visningssträngar i PDF/Word, riktiga tal i Excel."""

    headers: list[str]
    kinds: list[str]  # text | sek | pct | pp
    rows: list[list[Any]] = field(default_factory=list)

    def display(self) -> Table:
        def cell(value: Any, kind: str) -> Any:
            if not isinstance(value, Decimal):
                return value
            if kind == "sek":
                return format_sek(value)
            if kind == "sek_signed":
                return format_sek(value, signed=True)
            if kind == "pct":
                return format_percent(value.quantize(Decimal("0.1")))
            if kind == "pp":
                return fmt_change(value, "pp")
            return str(value)

        return Table(
            self.headers,
            [[cell(v, k) for v, k in zip(row, self.kinds, strict=True)] for row in self.rows],
            numeric_cols={i for i, k in enumerate(self.kinds) if k != "text"},
        )

    def excel(self) -> Table:
        return Table(
            self.headers,
            [[v for v in row] for row in self.rows],
            numeric_cols={i for i, k in enumerate(self.kinds) if k != "text"},
        )


@dataclass(slots=True)
class _Part:
    item: DifferenceItem | None
    title: str
    kind: str
    summary: str
    comment: str | None
    grid: _Grid | None = None
    notes: list[str] = field(default_factory=list)
    extra_sections: list[tuple[str, _Grid]] = field(default_factory=list)
    structure: MetricStructure | None = None


def _clean(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    value = text.strip()
    if len(value) > limit:
        raise ReportSelectionError(f"Texten är för lång (högst {limit} tecken).")
    return value or None


def _metric_grid(
    item: DifferenceItem,
    differences: DifferenceSet,
    current: str,
    previous: str,
    names: dict[int, str],
    hidden: AccountSet | None,
) -> _Grid:
    explanation = differences.explanations[item.code]
    unit = REGISTRY[item.code].unit.value
    if unit == "SEK" and len(explanation.components) == 1:
        # Ett nyckeltal med en enda byggsten (t.ex. kassa) visas per konto i stället.
        component = explanation.components[0]
        rows = {
            a: (component.current_accounts.get(a, ZERO), component.previous_accounts.get(a, ZERO))
            for a in set(component.current_accounts) | set(component.previous_accounts)
        }
        return _accounts_to_grid(rows, names, hidden, current, previous, component.current, component.previous)
    effect_kind = "sek_signed" if unit == "SEK" else "pp"
    grid = _Grid(["Post", current, previous, "Påverkan"], ["text", "sek", "sek", effect_kind])
    components = [c for c in explanation.components if c.effect or c.current or c.previous]
    components.sort(key=lambda c: -abs(c.effect))
    shown, rest = components[:TOP_ROWS], components[TOP_ROWS:]
    for c in shown:
        grid.rows.append([c.display_label, c.current, c.previous, c.effect])
    if rest:
        grid.rows.append(
            [
                f"Övriga poster ({len(rest)} st)",
                sum((c.current for c in rest), ZERO),
                sum((c.previous for c in rest), ZERO),
                sum((c.effect for c in rest), ZERO),
            ]
        )
    if explanation.change is not None:
        grid.rows.append(["Summa förändring", "", "", explanation.change])
    return grid


def _account_grid(
    item: DifferenceItem,
    index: LedgerIndex,
    pair: ComparisonPair,
    differences: DifferenceSet,
    hidden: AccountSet | None,
    current: str,
    previous: str,
) -> _Grid:
    names = {a: acc.name for a, acc in index.ledger.accounts.items()}
    accounts = AccountSet.of(*item.accounts) if item.accounts else AccountSet.of()
    if item.kind == "line" and item.extra.get("statement") == "balance":
        now = {a: v for a, v in index.balances_at(pair.current.end, accounts).items()}
        before = {a: v for a, v in index.balances_at(pair.previous.end, accounts).items()}
        sign = Decimal(1) if item.extra.get("role") == "asset" else Decimal(-1)
    else:
        now = index.movement_by_account(accounts, pair.current)
        before = index.movement_by_account(accounts, pair.previous)
        # Resultatrader visas som i resultaträkningen (intäkt +, kostnad −), kategorier som kostnad +.
        sign = Decimal(1) if item.kind == "category" else Decimal(-1)
    rows: dict[int, tuple[Decimal, Decimal]] = {
        a: (now.get(a, ZERO) * sign, before.get(a, ZERO) * sign) for a in set(now) | set(before)
    }
    return _accounts_to_grid(rows, names, hidden, current, previous, item.current, item.previous)


def _accounts_to_grid(
    rows: dict[int, tuple[Decimal, Decimal]],
    names: dict[int, str],
    hidden: AccountSet | None,
    current: str,
    previous: str,
    total_now: Decimal | None,
    total_before: Decimal | None,
) -> _Grid:
    grid = _Grid(["Konto", "Namn", current, previous, "Förändring"], ["text", "text", "sek", "sek", "sek_signed"])
    visible = {a: v for a, v in rows.items() if hidden is None or a not in hidden}
    masked = {a: v for a, v in rows.items() if hidden is not None and a in hidden}
    ordered = sorted(visible, key=lambda a: (-abs(visible[a][0] - visible[a][1]), a))
    for a in ordered[:TOP_ROWS]:
        cur, prev = visible[a]
        grid.rows.append([str(a), names.get(a, f"Konto {a}"), cur, prev, cur - prev])
    rest = ordered[TOP_ROWS:]
    if rest:
        cur = sum((visible[a][0] for a in rest), ZERO)
        prev = sum((visible[a][1] for a in rest), ZERO)
        grid.rows.append(["", f"Övriga konton ({len(rest)} st)", cur, prev, cur - prev])
    if masked:
        cur = sum((v[0] for v in masked.values()), ZERO)
        prev = sum((v[1] for v in masked.values()), ZERO)
        grid.rows.append(["", "Lönekonton, sammanslagna", cur, prev, cur - prev])
    if total_now is not None and total_before is not None:
        grid.rows.append(["", "Summa", total_now, total_before, total_now - total_before])
    return grid


def _voucher_grid(item: DifferenceItem, index: LedgerIndex, pair: ComparisonPair, hidden: AccountSet | None) -> _Grid:
    grid = _Grid(["Period", "Verifikation", "Datum", "Konto", "Text", "Belopp"], ["text"] * 5 + ["sek"])
    wanted = set(item.accounts) - ({a for a in item.accounts if a in hidden} if hidden is not None else set())
    for period in (pair.current, pair.previous):
        rows = [
            (voucher, row)
            for voucher in index.vouchers_in(period)
            for row in voucher.effective_rows
            if row.account in wanted
        ]
        rows.sort(key=lambda vr: (-abs(vr[1].amount), str(vr[0].key)))
        for voucher, row in rows[:TOP_VOUCHERS]:
            grid.rows.append(
                [
                    period.label,
                    str(voucher.key),
                    voucher.row_date(row).isoformat(),
                    str(row.account),
                    (row.text or voucher.text)[:80],
                    row.amount,
                ]
            )
    return grid


def _structure_grid(structure: MetricStructure) -> _Grid:
    labels = [p.short + (" *" if p.open else "") for p in structure.periods]
    grid = _Grid(["Post", *labels], ["text"] * (len(labels) + 1))
    grid.rows.append([structure.label, *[p.display for p in structure.periods]])
    for row in structure.rows:
        cells: list[Any] = [row.label]
        for value, share in zip(row.values, row.shares, strict=True):
            if value is None:
                cells.append("–")
                continue
            text = format_sek(value)
            if share is not None:
                text += f" ({format_percent(share)})"
            cells.append(text)
        grid.rows.append(cells)
    return grid


def _structure_notes(structure: MetricStructure) -> list[str]:
    notes = list(structure.warnings)
    if structure.base_label:
        notes.append(f"Inom parentes: andel av {structure.base_label}.")
    unit = "SEK" if structure.unit == "SEK" else "pp"
    overall = structure.overall or (structure.steps[-1] if structure.steps else None)
    if overall is not None and overall.change is not None:
        top = sorted(overall.components, key=lambda c: -abs(c[2]))[:3]
        drivers = ", ".join(f"{label.lower()} {fmt_change(effect, unit)}" for _code, label, effect in top if effect)
        notes.append(
            f"Förändring {overall.previous_label} → {overall.current_label}: {fmt_change(overall.change, unit)}"
            + (f" (störst: {drivers})." if drivers else ".")
        )
    if any(p.open for p in structure.periods):
        notes.append("* = perioden är inte avslutad.")
    return notes


def resolve_selection(
    selection: list[SelectedItem],
    differences: DifferenceSet,
    *,
    audience: str,
) -> list[tuple[SelectedItem, DifferenceItem | None]]:
    """Kontrollera urvalet mot serverns egna beräknade poster (klientens siffror används aldrig)."""
    if audience not in AUDIENCES:
        raise ReportSelectionError("Mottagare ska vara intern eller kund.")
    if not selection:
        raise ReportSelectionError("Välj minst en jämförelse till rapporten.")
    if len(selection) > MAX_ITEMS:
        raise ReportSelectionError(f"Högst {MAX_ITEMS} poster per rapport.")
    seen: set[str] = set()
    out: list[tuple[SelectedItem, DifferenceItem | None]] = []
    for chosen in selection:
        key = f"{chosen.id}|{chosen.series}|{chosen.count}"
        if key in seen:
            continue
        seen.add(key)
        _clean(chosen.comment, MAX_COMMENT)
        if chosen.id.startswith("structure:"):
            code = chosen.id.split(":", 1)[1]
            if code not in REGISTRY:
                raise ReportSelectionError(f"Okänt nyckeltal: {code}")
            series = chosen.series or "months"
            if series not in SERIES_KINDS:
                raise ReportSelectionError(f"Okänd serietyp: {series}")
            maximum = SERIES_KINDS[series][1]
            if chosen.count is not None and not 2 <= chosen.count <= maximum:
                raise ReportSelectionError(f"Antal perioder ska vara mellan 2 och {maximum}.")
            out.append((chosen, None))
            continue
        item = differences.get(chosen.id)
        if item is None:
            raise ReportSelectionError(f"Okänd jämförelse: {chosen.id}")
        if not item.selectable:
            raise ReportSelectionError(f"{item.title} saknar underlag för jämförelsen och kan inte tas med.")
        if audience == "client" and item.audience != "client":
            raise ReportSelectionError(f"{item.title} är ett internt analysfynd och kan inte tas med i en kundrapport.")
        out.append((chosen, item))
    return out


def build_comparison_report(
    index: LedgerIndex,
    pair: ComparisonPair,
    differences: DifferenceSet,
    selection: list[SelectedItem],
    *,
    company_name: str,
    org_number: str | None,
    audience: str,
    mapping: StatementMapping,
    rates: RateTable,
    firm_name: str,
    prepared_by: str | None = None,
    title: str | None = None,
    intro: str | None = None,
    hidden_accounts: AccountSet | None = None,
    include_key_figures: bool = True,
    source_fingerprint: str | None = None,
    now: datetime | None = None,
) -> tuple[Document, list[tuple[str, Table]]]:
    """Bygg rapporten. Returnerar dokumentet (PDF/Word) och flikar för Excel."""
    resolved = resolve_selection(selection, differences, audience=audience)
    title = _clean(title, MAX_TITLE)
    intro = _clean(intro, MAX_INTRO)
    client = audience == "client"
    if client:
        # Kundrapporten visar aldrig enskilda lönekonton, oavsett användarens behörighet.
        from redovisningai.review.analysis import PAYROLL

        hidden_accounts = PAYROLL
    now = now or datetime.now()
    current_label, previous_label = pair.current.label, pair.previous.label
    parts: list[_Part] = []
    for chosen, item in resolved:
        comment = _clean(chosen.comment, MAX_COMMENT)
        if item is None:
            code = chosen.id.split(":", 1)[1]
            series = chosen.series or "months"
            count = chosen.count or SERIES_KINDS[series][2]
            periods = period_series(series, pair.current.end, count, index.ledger)
            structure = metric_structure(
                code,
                index,
                periods,
                series=series,
                mapping=mapping,
                rates=rates,
                hidden_accounts=hidden_accounts,
            )
            parts.append(
                _Part(
                    None,
                    f"{structure.label} – {SERIES_KINDS[series][0].lower()}",
                    "structure",
                    f"{structure.label}, {SERIES_KINDS[series][0].lower()}: "
                    + "; ".join(f"{p.short}{' (pågående)' if p.open else ''} {p.display}" for p in structure.periods)
                    + ".",
                    comment,
                    _structure_grid(structure),
                    _structure_notes(structure),
                    structure=structure,
                )
            )
            continue
        part = _Part(item, item.title, item.kind, item.summary, comment, notes=list(item.warnings))
        if item.kind == "metric":
            names = {a: acc.name for a, acc in index.ledger.accounts.items()}
            part.grid = _metric_grid(item, differences, current_label, previous_label, names, hidden_accounts)
            part.notes.append(f"Så räknas det: {REGISTRY[item.code].formula}")
        elif item.kind in ("line", "category"):
            part.grid = _account_grid(item, index, pair, differences, hidden_accounts, current_label, previous_label)
            if not client:
                vouchers = _voucher_grid(item, index, pair, hidden_accounts)
                if vouchers.rows:
                    part.extra_sections.append((f"Största verifikationer – {item.title}", vouchers))
        elif item.kind == "finding":
            refs = item.extra.get("references") or []
            grid = _Grid(["Period", "Verifikation", "Datum", "Källrad"], ["text"] * 4)
            for ref in refs:
                grid.rows.append(
                    [
                        str(ref.get("period") or ""),
                        str(ref.get("voucher") or ""),
                        str(ref.get("date") or ""),
                        str(ref.get("source_line") or ""),
                    ]
                )
            part.grid = grid if grid.rows else None
            part.notes.append(item.reason)
        parts.append(part)

    sections: list[Section] = []
    status_text = (
        "Fullständigt underlag för båda perioderna."
        if pair.status is FactStatus.CALCULATED
        else "; ".join(pair.warnings) or pair.status.value
    )
    if pair.notices:
        status_text += " " + " ".join(pair.notices)
    about = [
        ["Bolag", company_name],
        ["Organisationsnummer", org_number or "–"],
        ["Period", current_label],
        ["Jämförelseperiod", previous_label],
        ["Underlag", status_text],
        ["Framtagen", f"{now:%Y-%m-%d %H:%M}" + (f" av {prepared_by}" if prepared_by else "")],
    ]
    if not client:
        about.append(["Beräkningsversion", CALC_VERSION])
        if source_fingerprint:
            about.append(["Källdata (fingeravtryck)", source_fingerprint[:16]])
    sections.append(Section("Om rapporten", table=Table(["Uppgift", "Värde"], about)))
    if intro:
        sections.append(Section("Inledning", paragraphs=[p for p in re.split(r"\n\s*\n", intro) if p.strip()]))
    sections.append(
        Section(
            "Sammanfattning",
            bullets=[p.summary + (f" Kommentar: {p.comment}" if p.comment else "") for p in parts],
        )
    )
    metric_parts_list = [p for p in parts if p.kind == "metric" and p.item is not None]
    if metric_parts_list:
        grid = _Grid(["Nyckeltal", current_label, previous_label, "Förändring"], ["text"] * 4)
        for p in metric_parts_list:
            assert p.item is not None
            unit = p.item.unit
            grid.rows.append(
                [
                    p.item.title,
                    fmt_value(p.item.current, unit),
                    fmt_value(p.item.previous, unit),
                    fmt_change(p.item.change, unit),
                ]
            )
        sections.append(Section("Valda nyckeltal", table=grid.display()))
    for number, p in enumerate(parts, start=1):
        paragraphs = [p.summary]
        if p.comment:
            paragraphs.append(f"Kommentar: {p.comment}")
        sections.append(
            Section(
                f"{number}. {p.title}",
                paragraphs=paragraphs,
                table=p.grid.display() if p.grid else None,
                note=" ".join(p.notes) if p.notes else None,
            )
        )
        for heading, grid in p.extra_sections:
            sections.append(Section(heading, table=grid.display()))
    if include_key_figures:
        all_grid = _Grid(["Nyckeltal", current_label, previous_label, "Förändring", "Status"], ["text"] * 5)
        for code, explanation in differences.explanations.items():
            unit = REGISTRY[code].unit.value
            all_grid.rows.append(
                [
                    REGISTRY[code].name,
                    fmt_value(explanation.current, unit),
                    fmt_value(explanation.previous, unit),
                    fmt_change(explanation.change, unit),
                    {"CALCULATED": "", "PARTIAL": "preliminär"}.get(explanation.status.value, "underlag saknas"),
                ]
            )
        sections.append(Section("Bilaga: samtliga nyckeltal", table=all_grid.display()))

    document = Document(
        title=f"{company_name} – {title or 'jämförelse'}",
        subtitle=f"{current_label} jämfört med {previous_label} · {firm_name}",
        sections=sections,
        footer=(
            f"Framtagen {now:%Y-%m-%d}. Alla belopp och nyckeltal är beräknade av RedovisningAI ur bokföringen – "
            "inga siffror är AI-genererade. Förändringsbryggorna summerar exakt till förändringen; avrundning sker "
            "bara i visningen."
        ),
        classification="KUNDRAPPORT" if client else "INTERN – FÅR INTE LÄMNAS TILL KUND",
        created_at=now,
    )

    summary = _Grid(
        ["Nr", "Post", "Typ", "Sammanfattning", "Kommentar", current_label, previous_label, "Förändring"],
        ["text"] * 8,
    )
    kind_sv = {
        "metric": "Nyckeltal",
        "line": "Rad",
        "category": "Kostnadskategori",
        "finding": "Analysfynd",
        "structure": "Över tid",
    }
    for number, p in enumerate(parts, start=1):
        item = p.item
        summary.rows.append(
            [
                str(number),
                p.title,
                kind_sv.get(p.kind, p.kind),
                p.summary,
                p.comment or "",
                item.current if item is not None and item.current is not None else "",
                item.previous if item is not None and item.previous is not None else "",
                item.change if item is not None and item.change is not None else "",
            ]
        )
    sheets: list[tuple[str, Table]] = [("Sammanfattning", summary.excel())]
    for number, p in enumerate(parts, start=1):
        if p.grid is not None:
            sheets.append((_sheet_name(number, p.title), p.grid.excel()))
    return document, sheets


def _sheet_name(number: int, title: str) -> str:
    cleaned = re.sub(r"[\[\]:*?/\\]", " ", title)
    return f"{number} {cleaned}"[:31]
