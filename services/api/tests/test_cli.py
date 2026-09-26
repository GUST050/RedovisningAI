"""Kommandoraden (Fas 0): convert, compare och analyze med olika källformat."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from redovisningai.cli import main
from redovisningai.devdata.generator import DEMO_PROFILES, generate
from redovisningai.sie.writer import write_sie4


@pytest.fixture(scope="module")
def files(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("cli")
    ledger = generate(DEMO_PROFILES[1], date(2026, 9, 30)).ledger
    out: dict[str, Path] = {}
    for year in ledger.years:
        path = root / f"lilja_{year.fiscal_year.label}.se"
        path.write_bytes(write_sie4(ledger, year))
        out[year.fiscal_year.label] = path
    current = next(y for y in ledger.years if y.fiscal_year.start == date(2026, 1, 1))
    lines = ["Vernr;Datum;Konto;Belopp;Text"]
    for voucher in current.vouchers:
        for row in voucher.effective_rows:
            lines.append(
                f"{voucher.series}{voucher.number};{voucher.date};{row.account};{row.amount};{voucher.text.replace(';', ' ')}"
            )
    csv_path = root / "lilja_2026.csv"
    csv_path.write_text("\n".join(lines), encoding="utf-8")
    out["csv"] = csv_path
    out["root"] = root
    return out


def test_convert_writes_standard_format(files: dict[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    target = files["root"] / "bokforing.json"
    assert main(["convert", str(files["2025"]), str(files["csv"]), "--out", str(target)]) == 0
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["format"] == "redovisningai.ledger"
    starts = [y["start"] for y in data["fiscal_years"]]
    assert starts == ["2024-01-01", "2025-01-01", "2026-01-01"]
    assert data["fiscal_years"][2]["opening_balances_status"] == "missing"
    assert "verifikationer" in capsys.readouterr().out


def test_compare_writes_report_with_recommended_items(
    files: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    out_dir = files["root"] / "rapport"
    code = main(
        [
            "compare",
            str(files["2025"]),
            str(files["2026"]),
            "--period",
            "YTD:2026-09",
            "--structure",
            "operating_margin:same_month:3",
            "--format",
            "docx",
            "--out",
            str(out_dir),
        ]
    )
    assert code == 0
    printed = capsys.readouterr().out
    assert "Viktigaste skillnaderna" in printed and "Nettoomsättning" in printed
    reports = list(out_dir.glob("*.docx"))
    assert len(reports) == 1 and reports[0].stat().st_size > 5000


def test_compare_client_report_rejects_internal_items(files: dict[str, Path]) -> None:
    with pytest.raises(SystemExit, match="Okänd jämförelse"):
        main(
            [
                "compare",
                str(files["2026"]),
                "--period",
                "2026-09",
                "--audience",
                "client",
                "--items",
                "finding:possible_duplicate:x",
                "--out",
                str(files["root"] / "kund"),
            ]
        )


def test_analyze_accepts_csv_together_with_sie(files: dict[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    out_dir = files["root"] / "analys"
    assert main(["analyze", str(files["2025"]), str(files["csv"]), "--period", "2026-09", "--out", str(out_dir)]) == 0
    assert any(p.suffix == ".xlsx" for p in out_dir.iterdir())
    assert "ärenden" in capsys.readouterr().out


def test_eval_accepts_openai_and_reports_missing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    import redovisningai.config as config

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("RAI_OPENAI_API_KEY", raising=False)
    # Ingen .env: testet får aldrig råka anropa en riktig leverantör.
    monkeypatch.setattr(config, "get_settings", lambda: config.Settings(_env_file=None))
    with pytest.raises(SystemExit, match="AI kunde inte konfigureras: OpenAI kräver OPENAI_API_KEY"):
        main(["eval", "--provider", "openai"])
