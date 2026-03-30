"""Tests for Database — new persona tables + KV store."""
import json
import pytest


def test_persona_tables_created(db):
    """All 4 new persona tables should exist after ensure_all_tables."""
    tables = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    table_names = {r["name"] for r in tables}
    assert "persona_config" in table_names
    assert "search_rounds" in table_names
    assert "review_decisions" in table_names
    assert "persona_analyses" in table_names


def test_skill_tables_created(db):
    tables = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    table_names = {r["name"] for r in tables}
    assert "skills" in table_names
    assert "skill_versions" in table_names
    assert "strategies" in table_names
    assert "strategy_runs" in table_names
    assert "followed_channels" in table_names
    assert "scoring_params" in table_names


def test_persona_kv_roundtrip(db):
    db.save_persona_kv("test_persona", "config", '{"model": "qwen2.5:7b"}')
    result = db.get_persona_kv("test_persona", "config")
    assert result == '{"model": "qwen2.5:7b"}'

    parsed = json.loads(result)
    assert parsed["model"] == "qwen2.5:7b"


def test_persona_kv_upsert(db):
    db.save_persona_kv("p1", "key1", '"v1"')
    db.save_persona_kv("p1", "key1", '"v2"')
    assert db.get_persona_kv("p1", "key1") == '"v2"'


def test_persona_kv_missing(db):
    assert db.get_persona_kv("nonexistent", "key") is None


def test_persona_kv_isolation(db):
    db.save_persona_kv("p1", "config", '"a"')
    db.save_persona_kv("p2", "config", '"b"')
    assert db.get_persona_kv("p1", "config") == '"a"'
    assert db.get_persona_kv("p2", "config") == '"b"'


def test_save_search_round(db):
    rid = db.save_search_round(
        persona_id="test",
        strategy_run_id=None,
        round_number=0,
        query="test query",
        original_query="test query",
        result_count=5,
        avg_views=10000,
        quality_score=0.7,
        was_refined=False,
        quota_units_used=100,
    )
    assert rid > 0


def test_save_review_decision(db):
    rid = db.save_review_decision(
        persona_id="test",
        strategy_run_id=None,
        youtube_video_id="abc123",
        strategy_name="gaming_deep_dive",
        decision="approved",
        original_title="Test Video",
        original_desc="Test description",
    )
    assert rid > 0

    decisions = db.get_review_decisions("test")
    assert len(decisions) == 1
    assert decisions[0]["youtube_video_id"] == "abc123"
    assert decisions[0]["decision"] == "approved"


def test_save_persona_analysis(db):
    rid = db.save_persona_analysis(
        persona_id="test",
        summary_json='{"patterns": []}',
        total_runs_analyzed=10,
        success_rate=0.5,
        updates_applied="updated youtube_principles",
    )
    assert rid > 0


def test_strategy_run_with_persona_id(db):
    """strategy_runs now supports persona_id column."""
    sid = db.add_strategy("test_strat", "test description", persona_id="sarcastic_ai")
    run_id = db.save_strategy_run(sid, "test query", persona_id="sarcastic_ai")
    assert run_id > 0

    rows = db._conn.execute(
        "SELECT persona_id FROM strategy_runs WHERE id = ?", (run_id,)
    ).fetchall()
    assert rows[0]["persona_id"] == "sarcastic_ai"


def test_strategy_persona_id_isolation(db):
    """Same strategy name, different persona_id — should both exist."""
    id1 = db.add_strategy("gaming", "desc1", persona_id="persona_a")
    id2 = db.add_strategy("gaming", "desc2", persona_id="persona_b")
    assert id1 != id2

    s1 = db.get_strategy("gaming", persona_id="persona_a")
    s2 = db.get_strategy("gaming", persona_id="persona_b")
    assert s1 is not None
    assert s2 is not None
    assert s1["description"] == "desc1"
    assert s2["description"] == "desc2"


def test_list_strategies_by_persona(db):
    db.add_strategy("s1", "d1", persona_id="p1")
    db.add_strategy("s2", "d2", persona_id="p1")
    db.add_strategy("s3", "d3", persona_id="p2")

    p1 = db.list_strategies(persona_id="p1")
    p2 = db.list_strategies(persona_id="p2")
    assert len(p1) == 2
    assert len(p2) == 1


def test_scoring_params_persona_id(db):
    db.save_scoring_params('{"test": 1}', source="test", persona_id="p1")
    db.save_scoring_params('{"test": 2}', source="test", persona_id="p2")

    p1 = db.get_scoring_params(persona_id="p1")
    p2 = db.get_scoring_params(persona_id="p2")
    assert p1 is not None
    assert p2 is not None
    assert p1["params_json"] == '{"test": 1}'
    assert p2["params_json"] == '{"test": 2}'


def test_get_approved_examples(db):
    """Approved/revised reviews should be returned, revised first."""
    db.save_review_decision(
        persona_id="test", strategy_run_id=None,
        youtube_video_id="v1", strategy_name="gaming",
        decision="approved", original_title="Original 1", original_desc="",
        final_title="Final 1", final_desc="Desc 1",
    )
    db.save_review_decision(
        persona_id="test", strategy_run_id=None,
        youtube_video_id="v2", strategy_name="tech",
        decision="revised", original_title="Original 2", original_desc="",
        final_title="Final 2", final_desc="Desc 2",
        feedback_rounds_json='[{"feedback": "more tsundere"}]',
    )
    db.save_review_decision(
        persona_id="test", strategy_run_id=None,
        youtube_video_id="v3", strategy_name="gaming",
        decision="rejected", original_title="Original 3", original_desc="",
        reject_reason="bad",
    )

    examples = db.get_approved_examples("test", limit=10)
    assert len(examples) == 2
    # Revised should come first
    assert examples[0]["decision"] == "revised"
    assert examples[0]["final_title"] == "Final 2"
    assert examples[1]["decision"] == "approved"
