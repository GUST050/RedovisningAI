"""Satstabell med giltighetsperioder (moms, arbetsgivaravgifter, bolagsskatt …)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

CATALOG_DIR = Path(__file__).parent / "catalog"


@dataclass(frozen=True, slots=True)
class Rate:
    code: str
    value: str
    valid_from: date
    valid_to: date | None
    source: str
    note: str = ""

    def valid_on(self, d: date) -> bool:
        return self.valid_from <= d and (self.valid_to is None or d <= self.valid_to)

    @property
    def decimal(self) -> Decimal:
        return Decimal(self.value)

    @property
    def decimals(self) -> list[Decimal]:
        return [Decimal(x.strip()) for x in self.value.split(",")]


class RateTable:
    def __init__(self, rates: list[Rate]) -> None:
        self.rates = rates

    @classmethod
    def load(cls, path: Path | None = None) -> RateTable:
        data = tomllib.loads((path or CATALOG_DIR / "rates.toml").read_text(encoding="utf-8"))
        rates = [
            Rate(
                code=r["code"],
                value=str(r["value"]),
                valid_from=date.fromisoformat(r["valid_from"]),
                valid_to=date.fromisoformat(r["valid_to"]) if r.get("valid_to") else None,
                source=r.get("source", ""),
                note=r.get("note", ""),
            )
            for r in data.get("rate", [])
        ]
        return cls(rates)

    def get(self, code: str, on: date) -> Rate:
        for r in self.rates:
            if r.code == code and r.valid_on(on):
                return r
        raise KeyError(f"Ingen sats {code!r} gäller {on}")

    def value(self, code: str, on: date) -> Decimal:
        return self.get(code, on).decimal

    def values(self, code: str, on: date) -> list[Decimal]:
        return self.get(code, on).decimals

    def all_valid(self, prefix: str, on: date) -> list[Rate]:
        return [r for r in self.rates if r.code.startswith(prefix) and r.valid_on(on)]


@lru_cache(maxsize=1)
def default_rates() -> RateTable:
    return RateTable.load()
