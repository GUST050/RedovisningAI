"""Gemensamt gränssnitt för datakällor. Kärnan känner bara till SIE-bytes / normaliserade data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RemoteFiscalYear:
    remote_id: str
    start: date
    end: date


@dataclass(frozen=True, slots=True)
class SieExport:
    fiscal_year: RemoteFiscalYear
    content: bytes


class ConnectorError(Exception):
    pass


class AuthorizationRevoked(ConnectorError):
    """Kunden har återkallat kopplingen – visas som kopplingsfel i portföljen."""


class SourceConnector(Protocol):
    source: str

    def fiscal_years(self) -> list[RemoteFiscalYear]: ...

    def export_sie4(self, year: RemoteFiscalYear) -> SieExport: ...
