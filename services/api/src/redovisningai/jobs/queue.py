"""Lägg AI-steg i bakgrundskön i stället för att köra dem i API-anropet (plan §9.8).

Importen och den deterministiska granskningen görs i anropet; AI-förslagen (A2) och den automatiska
analysen (A3) körs sedan av bakgrundsarbetaren. Går det inte att köa loggas det – importen är redan
klar och AI kan startas igen med "Granska igen".
"""

from __future__ import annotations

import logging
import uuid

from redovisningai.config import get_settings

log = logging.getLogger(__name__)


def enqueue_ai(org_id: uuid.UUID, company_id: uuid.UUID, periods: list[str]) -> bool:
    """Köa AI-steget för granskade perioder. Returnerar om ett jobb lades i kön."""
    if not periods or not get_settings().ai_enabled:
        return False
    from redovisningai.jobs.worker import ai_enrich_task, app

    try:
        with app.open():
            # Ett lås per bolag: AI-jobb för samma bolag körs i tur och ordning, aldrig samtidigt.
            ai_enrich_task.configure(lock=f"ai:{company_id}").defer(
                org_id=str(org_id), company_id=str(company_id), periods=periods
            )
    except Exception:
        log.exception("Kunde inte lägga AI-steget i kön för %s", company_id)
        return False
    return True
