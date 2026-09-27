"""Direktgenerering av kundrapport måste kräva separat godkännande av ärendefrågor."""

from redovisningai.reports.builders import client_report


def test_client_report_does_not_render_case_questions_without_explicit_approval() -> None:
    overview = {
        "company": {"name": "Testbolag"},
        "period": {"label": "September"},
        "sections": {"month": {"period": {"label": "September"}, "compare": {"label": "Augusti"}, "metrics": {}}},
    }
    statements = {"income": {"lines": [], "period": "September", "compare": "Augusti"}}
    meeting = {
        "summary": [],
        "questions": [],
        "case_questions": [{"case_key": "case", "question": "Intern känslig fråga"}],
    }

    document = client_report(overview, statements, meeting, "Byrån")
    assert all("Intern känslig fråga" not in bullet for section in document.sections for bullet in section.bullets)

    approved = client_report(overview, statements, meeting, "Byrån", approved_case_questions=meeting["case_questions"])
    assert any("Intern känslig fråga" in bullet for section in approved.sections for bullet in section.bullets)
