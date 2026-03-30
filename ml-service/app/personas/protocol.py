"""Persona Protocol — the only interface the Orchestrator knows."""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.db import Database


class RunContext(BaseModel):
    """Shared context constructed by the Orchestrator, passed into each persona.run()."""

    dry_run: bool = False
    no_upload: bool = False
    no_review: bool = False
    go_url: str = "http://localhost:8081"
    global_seen_ids: set[str] = Field(default_factory=set)
    quota_budget: int = 2000


class RunResult(BaseModel):
    """Unified result summary returned to the Orchestrator."""

    persona_id: str
    videos_discovered: int = 0
    videos_uploaded: int = 0
    videos_rejected: int = 0
    errors: list[str] = Field(default_factory=list)


class PerformanceSummary(BaseModel):
    """Placeholder — full definition lives in _shared/historian.py."""
    persona_id: str = ""


@runtime_checkable
class Persona(Protocol):
    """Orchestrator's sole interface to any persona.

    Internal implementation is entirely up to the persona — could be a
    7-phase pipeline, a 3-step script, or anything else.
    """

    @property
    def persona_id(self) -> str:
        """Unique identifier used for DB isolation (persona_id columns)."""
        ...

    async def run(self, db: "Database", context: RunContext) -> RunResult:
        """Execute this persona's full workflow."""
        ...

    def apply_historian_update(
        self, db: "Database", summary: PerformanceSummary,
    ) -> list[str]:
        """Receive Historian analysis and self-update.

        Returns a list of descriptions of what was actually updated (for audit).
        """
        ...
