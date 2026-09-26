"""Orkestrering av AI-uppgifter: pseudonymisering → modell → granskare → ev. omskrivning →
reservvariant. Allt loggas som spår (utan att lagra mer än nödvändigt)."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from redovisningai.ai.providers.base import (
    ModelProvider,
    ModelTier,
    ProviderError,
    ToolBudget,
    ToolSpec,
    Usage,
)
from redovisningai.ai.pseudonymize import Pseudonymizer
from redovisningai.ai.tasks import TASKS, AITask
from redovisningai.ai.verifier import VerificationResult, render_claims
from redovisningai.facts.model import FactStore

log = logging.getLogger(__name__)


class BudgetTracker(Protocol):
    def allow(self, org_id: str, task: str) -> bool: ...

    def record(self, org_id: str, task: str, usage: Usage) -> None: ...


class InMemoryBudget:
    """Enkel tokenbudget per byrå och månad (produktion: databasbaserad)."""

    def __init__(self, monthly_tokens: int = 5_000_000) -> None:
        self.monthly_tokens = monthly_tokens
        self.used: dict[tuple[str, str], int] = {}

    def _key(self, org_id: str) -> tuple[str, str]:
        return (org_id, datetime.now().strftime("%Y-%m"))

    def allow(self, org_id: str, task: str) -> bool:
        return self.used.get(self._key(org_id), 0) < self.monthly_tokens

    def record(self, org_id: str, task: str, usage: Usage) -> None:
        k = self._key(org_id)
        self.used[k] = self.used.get(k, 0) + usage.total


@dataclass(slots=True)
class AITrace:
    id: str
    task: str
    org_id: str
    company_id: str | None
    source: str  # ai | rules
    provider: str | None
    model: str | None
    region: str | None
    input_hash: str
    usage: dict[str, int]
    attempts: int
    rejected: list[dict[str, str]]
    downgraded: int
    error: str | None
    created_at: datetime
    package: dict[str, Any] | None = None  # sparas bara med kort retention
    output: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task": self.task,
            "org_id": self.org_id,
            "company_id": self.company_id,
            "source": self.source,
            "provider": self.provider,
            "model": self.model,
            "region": self.region,
            "input_hash": self.input_hash,
            "usage": self.usage,
            "attempts": self.attempts,
            "rejected": self.rejected,
            "downgraded": self.downgraded,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "tool_calls": self.tool_calls,
        }


@dataclass(slots=True)
class AIOutcome:
    task: str
    data: dict[str, Any]
    source: str  # "ai" | "rules"
    trace: AITrace
    verification: VerificationResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "data": self.data,
            "source": self.source,
            "trace_id": self.trace.id,
            "ai_generated": self.source == "ai",
        }


def _unmask(obj: Any, p: Pseudonymizer) -> Any:
    if isinstance(obj, str):
        return p.unmask(obj)
    if isinstance(obj, list):
        return [_unmask(x, p) for x in obj]
    if isinstance(obj, dict):
        return {k: _unmask(v, p) for k, v in obj.items()}
    return obj


CLAIM_FIELDS = ("claims", "summary", "questions", "root_cause", "rationale")


def render_output(data: dict[str, Any], store: FactStore) -> dict[str, Any]:
    """Rendera {f:id} i alla påståendelistor (även nästlade i ärenden)."""
    out: dict[str, Any] = {}
    for k, v in data.items():
        if k in CLAIM_FIELDS and isinstance(v, list):
            out[k] = render_claims(v, store)
        elif isinstance(v, list):
            out[k] = [render_output(x, store) if isinstance(x, dict) else x for x in v]
        elif isinstance(v, dict):
            out[k] = render_output(v, store)
        else:
            out[k] = v
    return out


class AIService:
    def __init__(
        self,
        provider: ModelProvider | None,
        *,
        budget: BudgetTracker | None = None,
        trace_sink: Callable[[AITrace], None] | None = None,
        keep_payloads: bool = True,
        max_output_tokens: int | None = None,
        max_tool_calls: int | None = None,
        max_attempts: int = 2,
    ) -> None:
        self.provider = provider
        self.budget = budget or InMemoryBudget()
        self.trace_sink = trace_sink
        self.keep_payloads = keep_payloads
        self.max_output_tokens = max_output_tokens
        self.max_tool_calls = max_tool_calls
        self.max_attempts = max_attempts

    @property
    def enabled(self) -> bool:
        return self.provider is not None

    def run(
        self,
        task_code: str,
        package: dict[str, Any],
        store: FactStore,
        *,
        org_id: str,
        company_id: str | None = None,
        names_to_mask: list[str] | None = None,
        allowed_identifiers: set[str] | None = None,
        tools: list[ToolSpec] | None = None,
        tool_budget: ToolBudget | None = None,
    ) -> AIOutcome:
        task: AITask = TASKS[task_code]
        allowed = set(allowed_identifiers or set())
        pseudo = Pseudonymizer(names_to_mask or [])
        content = pseudo.mask(task.user_content(package))
        input_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        trace = AITrace(
            id=str(uuid.uuid4()),
            task=task_code,
            org_id=org_id,
            company_id=company_id,
            source="rules",
            provider=None,
            model=None,
            region=None,
            input_hash=input_hash,
            usage={},
            attempts=0,
            rejected=[],
            downgraded=0,
            error=None,
            created_at=datetime.now(),
            package=package if self.keep_payloads else None,
        )
        usage = Usage()
        result_data: dict[str, Any] | None = None
        verification = VerificationResult()
        if self.provider is None:
            trace.error = "AI ej konfigurerad"
        elif not self.budget.allow(org_id, task_code):
            trace.error = "AI-budgeten för månaden är förbrukad"
        else:
            feedback = ""
            for attempt in range(self.max_attempts):
                trace.attempts = attempt + 1
                user = (
                    content
                    if not feedback
                    else (
                        content
                        + "\n\nFöljande påståenden underkändes av granskaren. Skriv om dem enligt reglerna:\n"
                        + feedback
                    )
                )
                try:
                    if task.spec.uses_tools:
                        res = self.provider.run_tools(
                            task=task_code,
                            tier=task.spec.tier,
                            system=task.spec.system,
                            user_content=user,
                            tools=tools or [],
                            schema=task.spec.schema,
                            budget=ToolBudget(
                                max_tool_calls=min((tool_budget or ToolBudget()).max_tool_calls, self.max_tool_calls)
                                if self.max_tool_calls is not None
                                else (tool_budget or ToolBudget()).max_tool_calls,
                                max_iterations=min((tool_budget or ToolBudget()).max_iterations, self.max_tool_calls)
                                if self.max_tool_calls is not None
                                else (tool_budget or ToolBudget()).max_iterations,
                            ),
                            max_tokens=min(task.spec.max_tokens, self.max_output_tokens)
                            if self.max_output_tokens is not None
                            else task.spec.max_tokens,
                        )
                    else:
                        res = self.provider.structured(
                            task=task_code,
                            tier=task.spec.tier,
                            system=task.spec.system,
                            user_content=user,
                            schema=task.spec.schema,
                            max_tokens=min(task.spec.max_tokens, self.max_output_tokens)
                            if self.max_output_tokens is not None
                            else task.spec.max_tokens,
                        )
                except ProviderError as exc:
                    trace.error = f"{type(exc).__name__}: {exc}"
                    log.warning("AI-uppgift %s misslyckades: %s", task_code, exc)
                    break
                usage.add(res.usage)
                trace.provider, trace.model, trace.region = res.provider, res.model, res.region
                if self.keep_payloads:
                    trace.tool_calls.extend(res.tool_calls)
                else:
                    # Keep an audit count/name without storing tool arguments,
                    # which can contain account, voucher or period details.
                    trace.tool_calls.extend({"tool": call.get("tool", "unknown")} for call in res.tool_calls)
                # Granska på pseudonymiserad text (återställda personnummer skulle annars se ut som
                # siffror), återställ sedan namn och uppgifter för visning.
                cleaned, verification = task.verify(res.data, package, store, allowed)
                cleaned = _unmask(cleaned, pseudo)
                trace.downgraded += verification.downgraded
                if verification.ok or attempt == self.max_attempts - 1:
                    trace.rejected = [
                        {
                            "text": r.claim.get("text", "") if self.keep_payloads else "",
                            "reason": r.reason if self.keep_payloads else "påstående underkändes av verifieraren",
                        }
                        for r in verification.rejected
                    ]
                    result_data = cleaned
                    break
                feedback = verification.feedback()
            self.budget.record(org_id, task_code, usage)
        trace.usage = usage.to_dict()
        if result_data is None:
            fb = task.fallback(package, store)
            result_data, verification = task.verify(fb, package, store, allowed)
            trace.source = "rules"
        else:
            trace.source = "ai"
        rendered = render_output(result_data, store)
        trace.output = rendered if self.keep_payloads else None
        if self.trace_sink:
            try:
                self.trace_sink(trace)
            except Exception:  # spårning får aldrig stoppa svaret
                log.exception("Kunde inte spara AI-spår")
        return AIOutcome(task_code, rendered, trace.source, trace, verification)


class FakeProvider:
    """Deterministisk leverantör för test och demo. Svarar med uppgiftens reservvariant, eller
    med en egen funktion per uppgift (för att testa granskaren)."""

    def __init__(
        self,
        responses: dict[str, Callable[[str], dict[str, Any]]] | None = None,
        store_for_fallback: FactStore | None = None,
    ) -> None:
        self.name = "fake"
        self.region: str | None = "local"
        self.responses = responses or {}
        self.calls: list[dict[str, Any]] = []
        self.store = store_for_fallback or FactStore()

    def _answer(self, task: str, user_content: str) -> dict[str, Any]:
        self.calls.append({"task": task, "user_content": user_content})
        fn = self.responses.get(task)
        if fn is not None:
            return fn(user_content)
        start = user_content.find("<kunddata>")
        end = user_content.find("</kunddata>")
        package = json.loads(user_content[start + len("<kunddata>") : end]) if start >= 0 else {}
        if task == "A5":
            package["question"] = user_content.split("\n", 1)[0]
        return TASKS[task].fallback(package, self.store)

    def structured(
        self,
        *,
        task: str,
        tier: ModelTier,
        system: str,
        user_content: str,
        schema: dict[str, Any],
        max_tokens: int = 16000,
    ) -> Any:
        from redovisningai.ai.providers.base import StructuredResult

        return StructuredResult(self._answer(task, user_content), Usage(100, 50), self.name, "fake-model", self.region)

    def run_tools(
        self,
        *,
        task: str,
        tier: ModelTier,
        system: str,
        user_content: str,
        tools: list[ToolSpec],
        schema: dict[str, Any],
        budget: ToolBudget,
        max_tokens: int = 16000,
    ) -> Any:
        from redovisningai.ai.providers.base import StructuredResult

        calls = []
        for t in tools[: budget.max_tool_calls]:
            if not t.input_schema.get("required"):
                t.handler({})
                calls.append({"tool": t.name, "input": {}})
        return StructuredResult(
            self._answer(task, user_content), Usage(200, 80), self.name, "fake-model", self.region, calls
        )
