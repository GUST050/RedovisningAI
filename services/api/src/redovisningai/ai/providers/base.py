"""Leverantörsabstraktion för språkmodeller.

Applikationen känner bara till `ModelProvider`. Modellval, region och lagringsvillkor är
konfiguration – ett modellbyte är en konfigurationsändring plus en eval-körning.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class ModelTier(StrEnum):
    """Nivåer i planen: liten (batch, klassning), mellan, stark (analys och text)."""

    SMALL = "small"
    MEDIUM = "medium"
    STRONG = "strong"


class ProviderError(Exception):
    """Fel som inte ska ge failover (t.ex. ogiltig förfrågan)."""

    def __init__(self, message: str = "", *, usage: Usage | None = None) -> None:
        super().__init__(message)
        # Ett svar som togs emot men inte gick att använda (t.ex. avbrutet vid tokengränsen)
        # debiteras ändå av leverantören – budgeten och AI-spåret ska räkna med det.
        self.usage = usage if usage is not None else Usage()


class ProviderUnavailable(ProviderError):
    """Tillfälligt fel (nätverk, 5xx, överbelastning) – försök med nästa leverantör."""


class RefusalError(ProviderError):
    """Modellen avböjde förfrågan (stop_reason = refusal)."""


@dataclass(slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens

    def to_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
        }


@dataclass(slots=True)
class StructuredResult:
    data: dict[str, Any]
    usage: Usage
    provider: str
    model: str
    region: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """Ett läsverktyg som AI:n får anropa. Handlern körs på servern med låst kund-id."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]


@dataclass(frozen=True, slots=True)
class ToolBudget:
    max_tool_calls: int = 8
    max_iterations: int = 6


class ModelProvider(Protocol):
    name: str
    region: str | None

    def structured(
        self,
        *,
        task: str,
        tier: ModelTier,
        system: str,
        user_content: str,
        schema: dict[str, Any],
        max_tokens: int = 16000,
    ) -> StructuredResult: ...

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
    ) -> StructuredResult: ...


class FailoverProvider:
    """Försök leverantörerna i tur och ordning vid tillfälliga fel (t.ex. regionalt avbrott)."""

    def __init__(self, providers: list[ModelProvider]) -> None:
        if not providers:
            raise ValueError("Minst en leverantör krävs")
        self.providers = providers
        self.name = "+".join(p.name for p in providers)
        self.region = providers[0].region

    def _call(self, method: str, **kwargs: Any) -> StructuredResult:
        last: Exception | None = None
        for p in self.providers:
            try:
                return getattr(p, method)(**kwargs)  # type: ignore[no-any-return]
            except ProviderUnavailable as exc:
                last = exc
                continue
        raise ProviderUnavailable(f"Alla leverantörer otillgängliga: {last}")

    def structured(self, **kwargs: Any) -> StructuredResult:
        return self._call("structured", **kwargs)

    def run_tools(self, **kwargs: Any) -> StructuredResult:
        return self._call("run_tools", **kwargs)
