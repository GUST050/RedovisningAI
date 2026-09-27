"""Sekretessprov: hitta kända namn i det som skickas till en AI-leverantör."""

from __future__ import annotations

import re
from collections.abc import Iterable


def strings(obj: object) -> str:
    """Alla strängvärden i ett JSON-liknande objekt (inte nycklarna), åtskilda av mellanslag."""
    if isinstance(obj, dict):
        return " ".join(strings(v) for v in obj.values())
    if isinstance(obj, list | tuple):
        return " ".join(strings(v) for v in obj)
    return obj if isinstance(obj, str) else ""


def leaks(text: str, names: Iterable[str]) -> list[str]:
    """Namnen som förekommer som hela ord i texten, oavsett skiftläge."""
    return [n for n in names if re.search(rf"(?<!\w){re.escape(n)}(?!\w)", text, re.IGNORECASE)]
