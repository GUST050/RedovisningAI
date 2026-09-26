from redovisningai.ai.evals import run_finding_evals


def test_synthetic_finding_eval_pins_grouping_and_incomplete_period_behavior() -> None:
    (result,) = run_finding_evals()

    assert result.passed, result.failures
    assert result.metrics["grouped_candidates"] == 1
    assert result.metrics["fact_references"] == 2
    assert result.metrics["incomplete_candidates"] == 0
    assert result.metrics["top_five"] <= 5
