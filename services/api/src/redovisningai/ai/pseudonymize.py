"""Pseudonymisering före modellanrop.

Personnummer (med Luhn-kontroll), e-post, telefonnummer och kända namn (t.ex. anställda,
ägare) ersätts med tokens som PERSON_1. Servern återställer dem i svaret innan det visas.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

PNR = re.compile(r"\b(?:19|20)?(\d{6})[-+]?(\d{4})\b")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
PHONE = re.compile(r"(?<!\d)(?:\+46[\s-]?|0)7[02369](?:[\s-]?\d){7}(?!\d)")


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(digits):
        n = int(ch) * (2 if i % 2 == 0 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


class Pseudonymizer:
    def __init__(self, names: Iterable[str] = ()) -> None:
        self._forward: dict[str, str] = {}
        self._reverse: dict[str, str] = {}
        self._counters: dict[str, int] = {}
        self._names = sorted({n.strip() for n in names if n and len(n.strip()) >= 3}, key=len, reverse=True)

    def _token(self, kind: str, value: str) -> str:
        if value in self._forward:
            return self._forward[value]
        self._counters[kind] = self._counters.get(kind, 0) + 1
        tok = f"{kind}_{self._counters[kind]}"
        self._forward[value] = tok
        self._reverse[tok] = value
        return tok

    def mask(self, text: str) -> str:
        def pnr(m: re.Match[str]) -> str:
            digits = m.group(1) + m.group(2)
            return self._token("PNR", m.group(0)) if luhn_ok(digits) else m.group(0)

        out = PNR.sub(pnr, text)
        out = EMAIL.sub(lambda m: self._token("EMAIL", m.group(0)), out)
        out = PHONE.sub(lambda m: self._token("TEL", m.group(0)), out)
        for name in self._names:
            out = re.sub(rf"\b{re.escape(name)}\b", lambda m: self._token("PERSON", m.group(0)), out, flags=re.I)
        return out

    def unmask(self, text: str) -> str:
        for tok in sorted(self._reverse, key=len, reverse=True):
            text = text.replace(tok, self._reverse[tok])
        return text

    @property
    def mapping_size(self) -> int:
        return len(self._forward)
