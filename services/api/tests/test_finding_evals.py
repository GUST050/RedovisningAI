import re

import pytest

from redovisningai.ai import verifier
from redovisningai.ai.evals import ZERO_METRICS, EvalResult, run_finding_evals, run_locked_case_evals
from redovisningai.devdata.finding_cases import locked_cases

# Plan Task 10: de scenarier som ska vara låsta syntetiska fall.
SCENARIOS = {
    "season",
    "one_off",
    "level_shift",
    "reversal",
    "duplicate",
    "uncertain_counterparty",
    "missing_month",
    "psaldo",
    "payroll_ptl",
    "prompt_injection",
}


@pytest.fixture(scope="module")
def locked() -> dict[str, EvalResult]:
    return {result.company: result for result in run_locked_case_evals()}


def test_synthetic_finding_eval_pins_grouping_and_incomplete_period_behavior() -> None:
    (result,) = run_finding_evals()

    assert result.passed, result.failures
    assert result.metrics["grouped_candidates"] == 1
    assert result.metrics["fact_references"] == 2
    assert result.metrics["incomplete_candidates"] == 0
    assert result.metrics["top_five"] <= 5


def test_locked_cases_cover_every_scenario_in_the_plan() -> None:
    assert {case.code for case in locked_cases()} == SCENARIOS


@pytest.mark.parametrize("code", sorted(SCENARIOS | {"budget_stop"}))
def test_locked_case_matches_its_answer_key(locked: dict[str, EvalResult], code: str) -> None:
    assert locked[code].passed, locked[code].failures


def test_locked_cases_pin_zero_errors_leaks_and_the_budget_stop(locked: dict[str, EvalResult]) -> None:
    totals = {key: sum(r.metrics.get(key, 0.0) for r in locked.values()) for key in (*ZERO_METRICS, "payload_leaks")}

    assert totals == dict.fromkeys(totals, 0.0)
    assert all(r.metrics["valid_accepted"] == 1 for r in locked.values() if "valid_accepted" in r.metrics)
    assert locked["budget_stop"].metrics["provider_calls"] == 0
    assert locked["budget_stop"].source == "rules"


def test_locked_evals_fail_when_the_business_cause_guard_is_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verifier, "BUSINESS_CAUSE", re.compile(r"(?!x)x"))  # regeln bortkopplad

    failed = [r for r in run_locked_case_evals() if not r.passed]

    assert failed and all("business_causes_accepted" in " ".join(r.failures) for r in failed)
