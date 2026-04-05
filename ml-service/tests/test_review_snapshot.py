"""Tests for review snapshot save/restore (Stage 3B crash recovery)."""
import json
import os
import sqlite3
import tempfile
from types import SimpleNamespace

import pytest

from app.server import (
    PipelineSession,
    PipelineStep,
    _serialize_candidate,
    _deserialize_candidate,
)


class TestSerializeCandidates:

    def test_strips_closures(self):
        job = {
            "_id": "vid1",
            "video_id": "vid1",
            "title": "Test",
            "description": "Desc",
            "strategy": "gaming",
            "tsundere_score": 7,
            "_approved": None,
            "_feedback": None,
            "_regenerate_fn": lambda *a: None,
            "candidate": SimpleNamespace(
                video_id="vid1",
                title="Original",
                channel="Ch",
                views=100000,
                duration_seconds=600,
                source_strategies=["gaming"],
            ),
        }
        serialized = _serialize_candidate(job)
        # Should be JSON-serializable
        json_str = json.dumps(serialized, ensure_ascii=False)
        assert "_regenerate_fn" not in json_str
        assert "candidate" not in serialized
        assert "_candidate_data" in serialized
        assert serialized["_candidate_data"]["views"] == 100000

    def test_roundtrip(self):
        job = {
            "_id": "vid1",
            "video_id": "vid1",
            "title": "Test Title",
            "description": "Desc",
            "strategy": "tech_teardown",
            "tsundere_score": 5,
            "_approved": True,
            "_feedback": "looks good",
            "_regenerate_fn": lambda *a: None,
            "candidate": SimpleNamespace(
                video_id="vid1",
                title="Original Title",
                channel="TechChannel",
                views=500000,
                duration_seconds=1200,
                source_strategies=["tech_teardown"],
            ),
        }
        serialized = _serialize_candidate(job)
        restored = _deserialize_candidate(serialized)

        assert restored["video_id"] == "vid1"
        assert restored["title"] == "Test Title"
        assert restored["strategy"] == "tech_teardown"
        assert restored["_approved"] is True
        assert restored["_regenerate_fn"] is None  # lost after serialize
        assert restored["candidate"].views == 500000
        assert restored["candidate"].channel == "TechChannel"

    def test_none_candidate(self):
        job = {
            "_id": "vid1",
            "video_id": "vid1",
            "title": "T",
            "description": "D",
            "strategy": "s",
            "tsundere_score": 5,
            "_approved": None,
            "_feedback": None,
            "_regenerate_fn": None,
            "candidate": None,
        }
        serialized = _serialize_candidate(job)
        restored = _deserialize_candidate(serialized)
        assert restored["candidate"] is None


class TestReviewSnapshotDB:

    def _make_db(self):
        from app.db import Database
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db = Database(path)
        db.connect()
        db.ensure_all_tables()
        return db, path

    def test_save_and_restore_snapshot(self):
        db, path = self._make_db()
        try:
            # Save a snapshot
            candidates = [
                {"_id": "v1", "video_id": "v1", "title": "T1", "strategy": "gaming"},
                {"_id": "v2", "video_id": "v2", "title": "T2", "strategy": "tech"},
            ]
            db._conn.execute(
                "INSERT INTO review_snapshots (session_id, run_id, candidates_json) VALUES (?, ?, ?)",
                ("sess-123", "sess-123", json.dumps(candidates, ensure_ascii=False)),
            )
            db._conn.commit()

            # Restore
            rows = db._conn.execute(
                "SELECT session_id, candidates_json FROM review_snapshots"
            ).fetchall()
            assert len(rows) == 1
            assert rows[0]["session_id"] == "sess-123"
            restored = json.loads(rows[0]["candidates_json"])
            assert len(restored) == 2
            assert restored[0]["video_id"] == "v1"

            # Delete after review complete
            db._conn.execute(
                "DELETE FROM review_snapshots WHERE session_id = ?",
                ("sess-123",),
            )
            db._conn.commit()
            rows = db._conn.execute(
                "SELECT * FROM review_snapshots"
            ).fetchall()
            assert len(rows) == 0
        finally:
            db.close()
            os.remove(path)

    def test_restore_creates_session(self):
        """Simulate what lifespan() does: restore snapshot into a PipelineSession."""
        db, path = self._make_db()
        try:
            candidates = [
                {
                    "_id": "v1",
                    "video_id": "v1",
                    "title": "Test",
                    "description": "D",
                    "strategy": "gaming",
                    "tsundere_score": 5,
                    "_approved": None,
                    "_feedback": None,
                    "_candidate_data": {
                        "video_id": "v1",
                        "title": "Original",
                        "channel": "Ch",
                        "views": 100000,
                        "duration_seconds": 600,
                        "source_strategies": ["gaming"],
                    },
                },
            ]
            db._conn.execute(
                "INSERT INTO review_snapshots (session_id, run_id, candidates_json) VALUES (?, ?, ?)",
                ("sess-abc", "sess-abc", json.dumps(candidates)),
            )
            db._conn.commit()

            # Simulate recovery
            rows = db._conn.execute(
                "SELECT session_id, candidates_json FROM review_snapshots"
            ).fetchall()
            for row in rows:
                sid = row["session_id"]
                restored_candidates = [
                    _deserialize_candidate(c)
                    for c in json.loads(row["candidates_json"])
                ]
                session = PipelineSession()
                session.session_id = sid
                session.phase = PipelineStep.review
                session.candidates = restored_candidates
                session._running = False

                # Verify session state
                assert session.session_id == "sess-abc"
                assert session.phase == PipelineStep.review
                assert len(session.candidates) == 1
                assert session.candidates[0]["video_id"] == "v1"
                assert session.candidates[0]["candidate"].views == 100000
                assert session.candidates[0]["_regenerate_fn"] is None
                assert session._running is False
        finally:
            db.close()
            os.remove(path)
