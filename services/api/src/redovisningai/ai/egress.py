"""Gemensam AI-gräns (plan §9.10): allt som skickas till en AI-leverantör passerar här.

Personer, personnummer, e-post och telefon maskeras med Pseudonymizer. Motparter ur bokföringen
byts mot koder M1, M2 … som är stabila inom en AI-körning – paketet, alla verktygssvar och
omförsöken. Kopplingen stannar i servern, och bara interna svar får namnen tillbaka.
"""

from __future__ import annotations

import json
import re
from collections.abc import Container, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from redovisningai.ai.providers.base import ToolSpec
from redovisningai.ai.pseudonymize import PNR, WORD_END, Pseudonymizer, luhn_ok
from redovisningai.analytics.counterparties import PREFIXES, counterparty_for_row, normalize_key
from redovisningai.domain.ledger import Ledger

if TYPE_CHECKING:
    from redovisningai.review.analysis import CompanyAnalysis

COUNTERPARTY_RE = re.compile(r"\{m:(M\d+)\}")
# {m:Mx} eller en fristående kod, i en genomgång så att ett insatt namn aldrig byts ut igen.
_CODE_OR_REFERENCE = re.compile(COUNTERPARTY_RE.pattern + rf"|(?<!\w)(M\d+){WORD_END}")

# Tillåtna toppnycklar per uppgift: exakt det projektionerna skickar. Övriga uppgifter maskeras ändå.
PACKAGE_FIELDS: dict[str, frozenset[str]] = {
    "A3": frozenset(
        {
            "period",
            "compare",
            "comparison_status",
            "comparison_warnings",
            "metrics",
            "bridge",
            "findings",
            "maturity",
            "open_cases",
            "facts",
        }
    ),
    "A4": frozenset(
        {
            "period",
            "compare",
            "comparison_status",
            "comparison_warnings",
            "metrics",
            "bridge",
            "maturity",
            "facts",
            "ask_client",
        }
    ),
    "A5": frozenset({"question", "default_period", "months_with_data"}),
}
FREE_TEXT_KEYS = frozenset({"text", "description"})  # verifikations-, rad- och fyndtext stannar i servern


class EgressViolation(Exception):
    """Något som inte får lämna servern var på väg till AI-leverantören; körningen stoppas."""


def render_counterparties(text: str, names: dict[str, str]) -> str:
    """Byt {m:Mx} och fristående utdelade koder ("M1", "M1s") mot namn; okända koder lämnas orörda."""

    def name(match: re.Match[str]) -> str:
        return names.get(match.group(1) or match.group(2), match.group(0))

    return _CODE_OR_REFERENCE.sub(name, text)


class CounterpartyPseudonyms:
    """Motparter → M1, M2 … i den ordning nycklarna först används; samma nyckel får samma kod."""

    def __init__(self, forms: dict[str, str] | None = None, display: dict[str, str] | None = None) -> None:
        self._forms = dict(forms or {})  # formen som den står i texten → motpartsnyckel
        self._display = dict(display or {})  # nyckel → visningsnamn
        self._codes: dict[str, str] = {}  # nyckel → kod
        by_fold: dict[str, tuple[str, str]] = {}
        for form, key in self._forms.items():
            by_fold.setdefault(form.casefold(), (form, key))
        self._keys = {fold: key for fold, (_, key) in by_fold.items()}
        # Längsta formen först, så att "Fastighets AB Kvarnen" går före "Kvarnen".
        alternatives = sorted((form for form, _ in by_fold.values()), key=lambda form: (-len(form), form))
        alternation = "|".join(re.escape(form) for form in alternatives)
        self._pattern = re.compile(rf"(?<!\w)(?:{alternation}){WORD_END}", re.IGNORECASE) if alternatives else None

    @classmethod
    def from_ledger(cls, ledger: Ledger, aliases: dict[str, str]) -> CounterpartyPseudonyms:
        """Motparterna som bokföringens texter ger, med och utan alias, och aliasens bekräftade namn.
        Nyckeln är också en form: nedbrytningar visar den ("amazon web" för "Amazon Web Services")."""
        # Kontonamn och textprefix ("Leverantörsfaktura" …) är inga namn. Inte heller former utan
        # bokstäver eller med ett enda tecken: de skulle göra om datum, belopp och ord till koder.
        reserved = {a.name.casefold() for a in ledger.accounts.values()} | {p.casefold() for p in PREFIXES}
        forms: dict[str, str] = {}
        display: dict[str, str] = {}

        def add(key: str, name: str, *variants: str) -> None:
            display.setdefault(key, name)
            for form in (name, *variants):
                text = form.strip()
                if len(text) > 1 and any(ch.isalpha() for ch in text) and text.casefold() not in reserved:
                    forms.setdefault(text, key)

        for alias_key, name in aliases.items():  # bekräftade namn först, så att de blir visningsnamn
            if key := normalize_key(name):
                add(key, name, key, alias_key)
        seen: set[tuple[str | None, str]] = set()
        for voucher in ledger.all_vouchers():
            for row in voucher.rows:
                if (row.text, voucher.text) in seen:
                    continue
                seen.add((row.text, voucher.text))
                guess = counterparty_for_row(voucher, row, aliases)
                if guess.key:
                    # Nedbrytningen gissar utan alias; samma motpart ska få samma kod där.
                    plain = counterparty_for_row(voucher, row) if aliases else guess
                    add(guess.key, guess.name, guess.surface, guess.key, plain.name, plain.surface, plain.key)
        return cls(forms, display)

    def code_for(self, key: str) -> str:
        if key not in self._codes:
            self._codes[key] = f"M{len(self._codes) + 1}"
        return self._codes[key]

    def mask(self, text: str) -> str:
        """Byt varje känd form (hela ord, oavsett skiftläge, även i genitiv) mot motpartens kod."""
        if self._pattern is None:
            return text
        return self._pattern.sub(lambda match: self.code_for(self._key_of(match.group(0))), text)

    def names(self) -> dict[str, str]:
        return {code: self._display.get(key, key) for key, code in self._codes.items()}

    def codes(self) -> frozenset[str]:
        return frozenset(self._codes.values())

    def known_names(self) -> frozenset[str]:
        return frozenset(self._forms)

    def _key_of(self, found: str) -> str:
        key = self._keys.get(found.casefold())
        if key is None:  # t.ex. "ı" och "i", som regex likställer men casefold() inte gör
            key = next(k for form, k in self._forms.items() if re.fullmatch(re.escape(form), found, re.IGNORECASE))
        return key


@dataclass(frozen=True, slots=True)
class EgressGuard:
    """Kontrollen vid leverantörsgränsen för en AI-körning: paketet, verktygssvaren och omförsöken."""

    task_code: str
    pseudo: Pseudonymizer
    pseudonyms: CounterpartyPseudonyms

    @classmethod
    def for_task(
        cls, task_code: str, analysis: CompanyAnalysis | None, *, person_names: Iterable[str] = ()
    ) -> EgressGuard:
        if analysis is None:
            return cls(task_code, Pseudonymizer(person_names), CounterpartyPseudonyms())
        return cls(
            task_code,
            Pseudonymizer([*analysis.ctx.person_names, *person_names]),
            CounterpartyPseudonyms.from_ledger(analysis.ledger, analysis.ctx.aliases),
        )

    def prepare_package(self, package: dict[str, Any]) -> dict[str, Any]:
        """Paketet som det får skickas: bara uppgiftens fält, varje strängvärde maskerat (aldrig nycklar)."""
        allowed = PACKAGE_FIELDS.get(self.task_code)
        if allowed is not None and not package.keys() <= allowed:
            outside = ", ".join(sorted(package.keys() - allowed))
            raise EgressViolation(f"{self.task_code}: fält utanför uppgiftens lista: {outside}")
        return {key: self._mask_values(value) for key, value in package.items()}

    def mask_text(self, text: str) -> str:
        return self.pseudonyms.mask(self.pseudo.mask(text))

    def ensure_clean(self, text: str) -> None:
        """Sista kontrollen före en sändning. Namn maskeras i värdena, så fast prompttext och
        JSON-nycklar prövas bara mot personnummer med giltig kontrollsiffra."""
        if any(luhn_ok(match.group(1) + match.group(2)) for match in PNR.finditer(text)):
            raise EgressViolation("personnummer i utgående data")

    def wrap_tool(self, tool: ToolSpec) -> ToolSpec:
        """Verktyget som modellen får: utan fritext och lönerader, med maskerade och kontrollerade svar."""
        from redovisningai.review.analysis import PAYROLL  # sen import: verifieraren ska kunna importera modulen

        def handler(inp: dict[str, Any]) -> Any:
            answer = self._scrub(tool.handler(inp), PAYROLL)
            self.ensure_clean(json.dumps(answer, ensure_ascii=False, default=str))
            return answer

        return ToolSpec(tool.name, tool.description, tool.input_schema, handler)

    def unmask(self, text: str, *, client_facing: bool) -> str:
        """Återställ personer och uppgifter; motpartsnamn bara i interna uppgifter."""
        text = self.pseudo.unmask(text)
        return text if client_facing else render_counterparties(text, self.pseudonyms.names())

    def _mask_values(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self._mask_values(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [self._mask_values(item) for item in value]
        if value is None or isinstance(value, bool | int | float):
            return value
        # Decimal, datum m.m. blir samma text som json.dumps(default=str) ger – men maskerad.
        return self.mask_text(value if isinstance(value, str) else str(value))

    def _scrub(self, value: Any, payroll: Container[int]) -> Any:
        if isinstance(value, dict):
            return {key: self._scrub(item, payroll) for key, item in value.items() if key not in FREE_TEXT_KEYS}
        if isinstance(value, list | tuple):
            return [self._scrub(item, payroll) for item in value if not _is_payroll_entry(item, payroll)]
        return self._mask_values(value)


def _is_payroll_entry(value: Any, payroll: Container[int]) -> bool:
    account = value.get("account") if isinstance(value, dict) else None
    return isinstance(account, int) and account in payroll
