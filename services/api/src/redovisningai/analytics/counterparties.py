"""Motpartsidentifiering ur verifikations- och radtexter (deterministisk grundnivå).

SIE4 saknar strukturerade motparter. Vi härleder ett motpartsnamn ur texten, t.ex.
"Leverantörsfaktura Microsoft Ireland 88213" → "Microsoft". Resultatet har en konfidens;
AI (A6) och konsulten kan förfina och bekräfta. Bekräftade alias går före heuristiken.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from redovisningai.domain.ledger import Row, Voucher

_PREFIXES = [
    "leverantörsfaktura",
    "leverantorsfaktura",
    "lev.faktura",
    "levfakt",
    "lev faktura",
    "faktura",
    "kundfaktura",
    "betalning",
    "inbetalning",
    "utbetalning",
    "kreditfaktura",
    "autogiro",
    "ag",
    "kortköp",
    "kortkop",
    "swish",
    "bg",
    "pg",
]
_SUFFIXES = {
    "ab",
    "(publ)",
    "publ",
    "hb",
    "kb",
    "ek",
    "för",
    "ltd",
    "limited",
    "inc",
    "gmbh",
    "as",
    "a/s",
    "oy",
    "bv",
    "llc",
    "sverige",
    "sweden",
    "ireland",
    "nordic",
    "norden",
    "europe",
    "emea",
    "international",
}
_NOISE = re.compile(r"\b(\d{3,}|\d{4}-\d{2}(-\d{2})?|nr|no|fakt|inv|ocr|ref|avg|period|\d{1,2}/\d{1,2})\b")


@dataclass(frozen=True, slots=True)
class CounterpartyGuess:
    key: str  # normaliserad nyckel för gruppering
    name: str  # visningsnamn
    confidence: float  # 0–1
    source: str  # "alias" | "heuristic" | "none"


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def normalize_key(name: str) -> str:
    s = _strip_accents(name.lower())
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    words = [w for w in s.split() if w not in _SUFFIXES]
    return " ".join(words).strip()


def guess_counterparty(text: str | None, aliases: dict[str, str] | None = None) -> CounterpartyGuess:
    """Gissa motpart ur en fritext. `aliases` mappar normaliserad nyckel → bekräftat namn."""
    if not text or not text.strip():
        return CounterpartyGuess("", "", 0.0, "none")
    t = text.strip()
    low = t.lower()
    for p in sorted(_PREFIXES, key=len, reverse=True):
        if low.startswith(p + " ") or low.startswith(p + ":"):
            t = t[len(p) + 1 :].strip(" :-")
            break
    cleaned = _NOISE.sub(" ", t)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -:,.")
    key = normalize_key(cleaned)
    if aliases:
        for k in (key, " ".join(key.split()[:2]), key.split()[0] if key else ""):
            if k and k in aliases:
                return CounterpartyGuess(normalize_key(aliases[k]), aliases[k], 0.98, "alias")
    if not key:
        return CounterpartyGuess("", "", 0.0, "none")
    words = cleaned.split()
    kept = [w for w in words if normalize_key(w)]
    name = " ".join(kept[:3]) if kept else cleaned
    # Kort, generisk text ("Diverse", "Omföring") är ingen motpart.
    generic = {"diverse", "omforing", "justering", "korrigering", "lon", "bankavgifter", "ranta", "momsredovisning"}
    if key.split()[0] in generic:
        return CounterpartyGuess("", "", 0.1, "none")
    confidence = 0.75 if len(key.split()) <= 3 else 0.55
    return CounterpartyGuess(" ".join(key.split()[:2]), name, confidence, "heuristic")


def counterparty_for_row(voucher: Voucher, row: Row, aliases: dict[str, str] | None = None) -> CounterpartyGuess:
    g = guess_counterparty(row.text, aliases)
    if g.key:
        return g
    return guess_counterparty(voucher.text, aliases)
