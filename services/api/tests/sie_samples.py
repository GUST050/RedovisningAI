"""Påhittade, minimala SIE4-filer för regressionstester (ingen riktig kunddata)."""

from __future__ import annotations

MONTHLY_RENT = 10_000


def _voucher(number: int, day: str, text: str, rows: list[tuple[int, int]]) -> list[str]:
    return [f'#VER A {number} {day} "{text}"', "{", *(f"#TRANS {acc} {{}} {amount}.00" for acc, amount in rows), "}"]


def minimal_sie_with_missing_rent() -> bytes:
    """Försäljning 50 000 kr/månad jan–sep 2026; lokalhyra (5010) jan–aug men inte i september."""
    lines = [
        "#FLAGGA 0",
        "#FORMAT PC8",
        "#SIETYP 4",
        '#PROGRAM "Handskriven testfil" 1.0',
        '#FNAMN "Påhittat Hyresbolag AB"',
        "#ORGNR 556000-0001",
        "#RAR 0 20260101 20261231",
        '#KONTO 1930 "Företagskonto"',
        '#KONTO 3001 "Försäljning"',
        '#KONTO 5010 "Lokalhyra"',
    ]
    number = 0
    for month in range(1, 10):
        day = f"2026{month:02d}05"
        number += 1
        lines += _voucher(number, day, "Försäljning", [(1930, 50_000), (3001, -50_000)])
        if month < 9:  # september saknar hyra
            number += 1
            lines += _voucher(number, day, "Hyra", [(5010, MONTHLY_RENT), (1930, -MONTHLY_RENT)])
    return ("\r\n".join(lines) + "\r\n").encode("cp437")
