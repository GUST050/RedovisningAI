"""Kalibreringsrapporten får bara innehålla aggregat från lokala SIE-data."""

from __future__ import annotations

import json
from decimal import Decimal

from redovisningai.cli import main
from redovisningai.devdata.bridge_calibration import calibrate_large_bookings
from sie_samples import sie_with_large_bookings


def test_calibration_counts_large_bookings_per_threshold_without_texts() -> None:
    report = calibrate_large_bookings(sie_with_large_bookings(), [Decimal("0.25"), Decimal("0.5")])
    assert report == {
        "periods": 3,
        "thresholds": {
            "0.25": {"signals": 4, "share_of_abs_amount": "0.8818"},
            "0.5": {"signals": 3, "share_of_abs_amount": "0.7455"},
        },
    }
    assert "Konsult Exempel" not in json.dumps(report, ensure_ascii=False)


def test_calibrate_bridge_command_prints_only_the_report(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "syntet.se"
    path.write_bytes(sie_with_large_bookings())
    assert main(["calibrate-bridge", str(path), "--shares", "0.25,0.5"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["periods"] == 3 and set(printed["thresholds"]) == {"0.25", "0.5"}


def test_offsetting_rows_in_one_voucher_do_not_count_as_a_large_booking() -> None:
    raw = b"\r\n".join(
        [
            b"#FLAGGA 0",
            b"#FORMAT PC8",
            b"#SIETYP 4",
            b"#RAR 0 20260101 20261231",
            b'#VER A 1 20260110 "Offset"',
            b"{",
            b"#TRANS 6550 {} 20000.00",
            b"#TRANS 6550 {} -20000.00",
            b"}",
        ]
    )
    report = calibrate_large_bookings(raw, [Decimal("0.25")])
    assert report == {"periods": 1, "thresholds": {"0.25": {"signals": 0, "share_of_abs_amount": "0.0000"}}}
