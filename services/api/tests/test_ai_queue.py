"""AI-stegen läggs i bakgrundskön i stället för att köras i API-anropet (plan §9.8, Task 8)."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from procrastinate.testing import InMemoryConnector

from redovisningai.config import get_settings
from redovisningai.jobs import worker
from redovisningai.jobs.queue import enqueue_ai

ORG, COMPANY = uuid.uuid4(), uuid.uuid4()


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemoryConnector]:
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    connector = InMemoryConnector()
    with worker.app.replace_connector(connector):
        yield connector


def test_enqueue_puts_one_job_with_only_ids_on_the_ai_queue(queue: InMemoryConnector) -> None:
    assert enqueue_ai(ORG, COMPANY, ["2026-08", "2026-09"]) is True

    (job,) = queue.jobs.values()
    assert job["task_name"].endswith("ai_enrich_task")
    assert job["queue_name"] == "ai" and job["lock"] == f"ai:{COMPANY}"
    # Jobbens argument innehåller bara id:n och perioder – aldrig kunddata.
    assert job["args"] == {"org_id": str(ORG), "company_id": str(COMPANY), "periods": ["2026-08", "2026-09"]}


@pytest.mark.parametrize(("ai_enabled", "periods"), [(False, ["2026-09"]), (True, [])], ids=["ai-av", "inga-perioder"])
def test_nothing_is_queued_without_ai_or_reviewed_periods(
    queue: InMemoryConnector, monkeypatch: pytest.MonkeyPatch, ai_enabled: bool, periods: list[str]
) -> None:
    monkeypatch.setattr(get_settings(), "ai_enabled", ai_enabled)
    assert enqueue_ai(ORG, COMPANY, periods) is False
    assert queue.jobs == {}


def test_a_queue_failure_is_logged_and_does_not_raise(
    queue: InMemoryConnector, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(**_: Any) -> Any:
        raise RuntimeError("kön är nere")

    monkeypatch.setattr(worker.ai_enrich_task, "configure", broken)
    with caplog.at_level(logging.ERROR, logger="redovisningai.jobs.queue"):
        assert enqueue_ai(ORG, COMPANY, ["2026-09"]) is False
    assert "AI-steget" in caplog.text
