"""Tests for LLM semaphore concurrency control (Stage 2)."""
import asyncio
import time

import pytest

from app.server import call_llm


class FakeBackend:
    """Backend that records call times to verify concurrency."""

    def __init__(self, delay: float = 0.1):
        self.delay = delay
        self.calls: list[dict] = []

    def chat(self, messages, **kwargs):
        start = time.monotonic()
        time.sleep(self.delay)
        end = time.monotonic()
        self.calls.append({"start": start, "end": end, "messages": messages})
        return "ok"


@pytest.mark.asyncio
async def test_call_llm_wraps_sync(monkeypatch):
    """call_llm should call backend.chat via to_thread and return result."""
    backend = FakeBackend(delay=0)
    result = await call_llm(backend, [{"role": "user", "content": "hi"}])
    assert result == "ok"
    assert len(backend.calls) == 1


@pytest.mark.asyncio
async def test_semaphore_limits_concurrency(monkeypatch):
    """With semaphore=2, 4 concurrent calls should overlap in pairs."""
    import app.server as srv
    original = srv.llm_semaphore
    srv.llm_semaphore = asyncio.Semaphore(2)

    try:
        backend = FakeBackend(delay=0.1)
        tasks = [
            call_llm(backend, [{"role": "user", "content": f"msg{i}"}])
            for i in range(4)
        ]
        await asyncio.gather(*tasks)
        assert len(backend.calls) == 4

        # With semaphore=2 and delay=0.1, total time should be ~0.2s (2 batches)
        # not ~0.1s (all parallel) or ~0.4s (all serial)
        starts = [c["start"] for c in backend.calls]
        ends = [c["end"] for c in backend.calls]
        total = max(ends) - min(starts)
        assert total >= 0.15, f"Too fast ({total:.3f}s) — semaphore not limiting"
        assert total < 0.5, f"Too slow ({total:.3f}s) — calls should overlap"
    finally:
        srv.llm_semaphore = original
