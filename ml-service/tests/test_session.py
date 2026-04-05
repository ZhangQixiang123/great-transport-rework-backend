"""Tests for PipelineSession (Stage 2)."""
import asyncio

import pytest

from app.server import PipelineSession, PipelineStep


class TestPipelineSession:

    def test_initial_state(self):
        s = PipelineSession()
        assert s.phase == PipelineStep.idle
        assert s.candidates == []
        assert s.error is None
        assert s.summary is None
        assert s.session_id

    def test_notify_updates_phase(self):
        s = PipelineSession()
        s.notify(PipelineStep.search)
        assert s.phase == PipelineStep.search

    def test_notify_updates_extra_data(self):
        s = PipelineSession()
        s.notify(PipelineStep.error, error="something broke")
        assert s.error == "something broke"
        assert s.phase == PipelineStep.error

    @pytest.mark.asyncio
    async def test_wait_for_update_wakes_on_notify(self):
        s = PipelineSession()

        async def notifier():
            await asyncio.sleep(0.05)
            s.notify(PipelineStep.scoring)

        asyncio.create_task(notifier())
        await asyncio.wait_for(s.wait_for_update(), timeout=2)
        assert s.phase == PipelineStep.scoring

    def test_to_status_dict(self):
        s = PipelineSession()
        s.notify(PipelineStep.done, summary={"uploaded": 3})
        d = s.to_status_dict()
        assert d["session_id"] == s.session_id
        assert d["step"] == "done"
        assert d["summary"] == {"uploaded": 3}
        assert d["candidates"] == []

    def test_sessions_isolated(self):
        s1 = PipelineSession()
        s2 = PipelineSession()
        s1.notify(PipelineStep.search)
        s2.notify(PipelineStep.review)
        assert s1.phase == PipelineStep.search
        assert s2.phase == PipelineStep.review
        assert s1.session_id != s2.session_id
