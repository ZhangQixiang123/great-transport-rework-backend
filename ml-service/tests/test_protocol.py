"""Tests for Persona Protocol and data models."""
from app.personas.protocol import Persona, RunContext, RunResult


class _DummyPersona:
    """Minimal Persona implementation for Protocol compliance testing."""

    @property
    def persona_id(self) -> str:
        return "test_dummy"

    async def run(self, db, context):
        return RunResult(persona_id=self.persona_id)

    def apply_historian_update(self, db, summary):
        return []


def test_persona_protocol_is_runtime_checkable():
    p = _DummyPersona()
    assert isinstance(p, Persona)


def test_run_context_defaults():
    ctx = RunContext()
    assert ctx.dry_run is False
    assert ctx.no_review is False
    assert ctx.go_url == "http://localhost:8081"
    assert ctx.global_seen_ids == set()
    assert ctx.quota_budget == 2000


def test_run_context_custom():
    ctx = RunContext(dry_run=True, quota_budget=500, global_seen_ids={"abc"})
    assert ctx.dry_run is True
    assert ctx.quota_budget == 500
    assert "abc" in ctx.global_seen_ids


def test_run_result_defaults():
    r = RunResult(persona_id="test")
    assert r.videos_discovered == 0
    assert r.videos_uploaded == 0
    assert r.videos_rejected == 0
    assert r.errors == []


def test_run_result_custom():
    r = RunResult(
        persona_id="test",
        videos_discovered=10,
        videos_uploaded=3,
        videos_rejected=2,
        errors=["timeout"],
    )
    assert r.videos_discovered == 10
    assert r.errors == ["timeout"]
