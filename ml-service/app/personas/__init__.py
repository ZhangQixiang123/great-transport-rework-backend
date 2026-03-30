"""Persona system — orchestrator and registry."""
import logging
from typing import Type

from .protocol import Persona, RunContext, RunResult
from ..db import Database

logger = logging.getLogger(__name__)

# Register all persona classes here.
from .sarcastic_ai import SarcasticAI

ALL_PERSONAS: list[Type[Persona]] = [SarcasticAI]


class PersonaOrchestrator:
    """Discovers and dispatches enabled personas."""

    def __init__(self, persona_classes: list[Type[Persona]] | None = None):
        self._persona_classes = persona_classes or ALL_PERSONAS

    async def run_all(
        self, db: Database, context: RunContext,
    ) -> dict[str, RunResult]:
        results: dict[str, RunResult] = {}
        for cls in self._persona_classes:
            persona = cls()
            pid = persona.persona_id

            if not self._is_enabled(db, pid):
                logger.info("Persona %s is disabled, skipping", pid)
                continue

            logger.info("Running persona: %s", pid)
            try:
                results[pid] = await persona.run(db, context)
            except Exception as e:
                logger.error("Persona %s failed: %s", pid, e)
                results[pid] = RunResult(persona_id=pid, errors=[str(e)])

        return results

    @staticmethod
    def _is_enabled(db: Database, persona_id: str) -> bool:
        row = db.get_persona_kv(persona_id, "enabled")
        return row != "false"
