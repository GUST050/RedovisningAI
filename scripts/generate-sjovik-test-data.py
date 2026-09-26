#!/usr/bin/env python3
"""Skapa reproducerbara SIE4-filer för en helt fiktiv testkund.

Filerna skrivs till var/testkund-sjovik (ignoreras av Git) och kan laddas upp
var för sig via kundens Data-flik. Ingen riktig kund- eller persondata används.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))

from redovisningai.devdata.generator import CompanyProfile, generate  # noqa: E402
from redovisningai.sie.parser import Severity, parse_sie  # noqa: E402
from redovisningai.sie.writer import write_sie4  # noqa: E402

AS_OF = date(2026, 9, 24)
PROFILE = CompanyProfile(
    name="Sjövik Elservice AB (TEST)",
    org_number="559999-9017",
    seed=20260924,
    industry="bygg",
    vat_period="quarter",
    first_year=2024,
    monthly_revenue=480_000,
    employees=3,
    monthly_salary=37_000,
    rent=18_000,
    monthly_accruals=True,
    plant_anomalies=False,
)


def main() -> None:
    out = ROOT / "var" / "testkund-sjovik"
    out.mkdir(parents=True, exist_ok=True)
    generated = generate(PROFILE, AS_OF)
    manifest = {
        "company": PROFILE.name,
        "org_number": PROFILE.org_number,
        "synthetic": True,
        "as_of": AS_OF.isoformat(),
        "files": [],
    }
    for year in generated.ledger.years:
        raw = write_sie4(generated.ledger, year, generated=AS_OF, include_previous_summary=False)
        doc = parse_sie(raw)
        assert doc.company_name == PROFILE.name
        assert doc.org_number == PROFILE.org_number
        assert len(doc.vouchers) == len(year.vouchers)
        assert not [i for i in doc.issues if i.severity is Severity.ERROR]
        assert all(v.balance == 0 for v in doc.vouchers)
        path = out / f"Sjovik-Elservice-AB-{year.fiscal_year.start.year}.se"
        path.write_bytes(raw)
        manifest["files"].append(
            {
                "name": path.name,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
                "vouchers": len(doc.vouchers),
                "period": [year.fiscal_year.start.isoformat(), year.fiscal_year.end.isoformat()],
                "issues": [issue.code for issue in doc.issues],
            }
        )
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
