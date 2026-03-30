"""
Tests for the Skill base class in app.skills.base.
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.skills.base import Skill


# ── Helpers ───────────────────────────────────────────────────────────


def _make_mock_db(skill_row=None):
    """Create a mock DB that returns skill_row from get_skill().

    If skill_row is None, get_skill returns None (trigger seed defaults).
    """
    db = MagicMock()
    db.get_skill.return_value = skill_row
    db.upsert_skill.return_value = 1
    db.get_skill_versions.return_value = []
    return db


def _make_mock_backend(response='{"result": "ok"}'):
    """Create a mock LLMBackend that returns a fixed response."""
    backend = MagicMock()
    backend.chat.return_value = response
    return backend


def _skill_row(
    name="test_skill", sys_prompt="You are helpful.", tmpl="{input}",
    schema='{"type":"object"}', version=1, skill_id=1,
):
    """Create a dict mimicking a skill DB row."""
    return {
        "id": skill_id,
        "name": name,
        "system_prompt": sys_prompt,
        "prompt_template": tmpl,
        "output_schema": schema,
        "version": version,
    }


# ── Loading from DB ──────────────────────────────────────────────────


class TestSkillLoadFromDB:
    def test_load_existing_skill(self):
        """When DB has the skill, Skill loads prompts from the row."""
        row = _skill_row(
            sys_prompt="Custom system",
            tmpl="Custom template: {input}",
            version=3,
        )
        db = _make_mock_db(skill_row=row)
        backend = _make_mock_backend()

        skill = Skill("test_skill", db, backend)

        assert skill.system_prompt == "Custom system"
        assert skill.prompt_template == "Custom template: {input}"
        assert skill.version == 3
        db.get_skill.assert_called_once_with("test_skill")
        db.upsert_skill.assert_not_called()

    def test_seed_defaults_when_not_in_db(self):
        """When skill is not in DB, Skill seeds defaults and upserts."""
        db = _make_mock_db(skill_row=None)
        backend = _make_mock_backend()

        skill = Skill("new_skill", db, backend)

        # Should have called upsert_skill with defaults
        db.upsert_skill.assert_called_once()
        call_args = db.upsert_skill.call_args
        assert call_args[0][0] == "new_skill"
        assert skill.version == 1

    def test_default_system_prompt(self):
        """_default_system_prompt returns a base prompt."""
        db = _make_mock_db(skill_row=None)
        backend = _make_mock_backend()

        skill = Skill("x", db, backend)
        assert "JSON" in skill.system_prompt

    def test_default_prompt_template(self):
        """_default_prompt_template contains {input} placeholder."""
        db = _make_mock_db(skill_row=None)
        backend = _make_mock_backend()

        skill = Skill("x", db, backend)
        assert "{input}" in skill.prompt_template


# ── execute() ─────────────────────────────────────────────────────────


class TestSkillExecute:
    def test_execute_formats_prompt_and_calls_backend(self):
        """execute() formats the template with context and calls backend.chat."""
        row = _skill_row(tmpl="Analyze this: {topic}")
        db = _make_mock_db(skill_row=row)
        backend = _make_mock_backend('{"score": 0.8}')

        skill = Skill("test", db, backend)
        result = skill.execute({"topic": "gaming videos"})

        assert result == {"score": 0.8}
        backend.chat.assert_called_once()
        call_kwargs = backend.chat.call_args
        messages = call_kwargs[1]["messages"] if "messages" in call_kwargs[1] else call_kwargs[0][0]
        # The user message should contain the formatted prompt
        user_msg = [m for m in messages if m["role"] == "user"][0]
        assert "gaming videos" in user_msg["content"]

    def test_execute_passes_json_schema(self):
        """execute() passes the output schema to backend.chat."""
        row = _skill_row(schema='{"type":"object","properties":{"score":{"type":"number"}}}')
        db = _make_mock_db(skill_row=row)
        backend = _make_mock_backend('{"score": 0.5}')

        skill = Skill("test", db, backend)
        skill.execute({"input": "test"})

        call_kwargs = backend.chat.call_args
        passed_schema = call_kwargs[1].get("json_schema") or call_kwargs[0][1] if len(call_kwargs[0]) > 1 else call_kwargs[1].get("json_schema")
        assert passed_schema is not None

    def test_execute_returns_parsed_json(self):
        """execute() returns parsed JSON dict."""
        row = _skill_row()
        db = _make_mock_db(skill_row=row)
        backend = _make_mock_backend('{"queries": [{"query": "test"}]}')

        skill = Skill("test", db, backend)
        result = skill.execute({"input": "anything"})

        assert isinstance(result, dict)
        assert "queries" in result


# ── reflect() ─────────────────────────────────────────────────────────


class TestSkillReflect:
    def test_reflect_returns_none_for_empty_outcomes(self):
        """reflect() returns None when outcomes list is empty."""
        row = _skill_row()
        db = _make_mock_db(skill_row=row)
        backend = _make_mock_backend()

        skill = Skill("test", db, backend)
        assert skill.reflect([]) is None

    def test_reflect_returns_none_when_no_reflection_prompt(self):
        """reflect() returns None when _build_reflection_prompt returns ''."""
        row = _skill_row()
        db = _make_mock_db(skill_row=row)
        backend = _make_mock_backend()

        skill = Skill("test", db, backend)
        # Base Skill._build_reflection_prompt returns ""
        result = skill.reflect([{"outcome": "data"}])
        assert result is None
        backend.chat.assert_not_called()

    def test_reflect_calls_backend_when_prompt_built(self):
        """reflect() calls backend.chat when _build_reflection_prompt returns content."""
        row = _skill_row()
        db = _make_mock_db(skill_row=row)
        backend = _make_mock_backend('{"analysis": "improve this"}')

        skill = Skill("test", db, backend)
        # Override _build_reflection_prompt to return something
        skill._build_reflection_prompt = MagicMock(return_value="Reflect on these outcomes.")

        result = skill.reflect([{"outcome": "data"}])
        backend.chat.assert_called_once()


# ── _update_prompt() ──────────────────────────────────────────────────


class TestSkillUpdatePrompt:
    def test_update_prompt_snapshots_and_persists(self):
        """_update_prompt() calls snapshot + update_skill_prompt."""
        row = _skill_row(sys_prompt="old sys", tmpl="old tmpl", version=2)
        db = _make_mock_db(skill_row=row)
        backend = _make_mock_backend()

        skill = Skill("test", db, backend)
        skill._update_prompt(
            {"system_prompt": "new sys", "prompt_template": "new tmpl"},
            changed_by="test_user",
            reason="improvement",
        )

        db.snapshot_skill_version.assert_called_once_with(
            "test", changed_by="test_user", reason="improvement",
        )
        db.update_skill_prompt.assert_called_once_with(
            "test", "new sys", "new tmpl",
        )
        assert skill.system_prompt == "new sys"
        assert skill.prompt_template == "new tmpl"
        assert skill.version == 3  # was 2, incremented

    def test_update_prompt_partial_system_only(self):
        """_update_prompt() with only system_prompt update keeps old template."""
        row = _skill_row(sys_prompt="old sys", tmpl="old tmpl")
        db = _make_mock_db(skill_row=row)
        backend = _make_mock_backend()

        skill = Skill("test", db, backend)
        skill._update_prompt(
            {"system_prompt": "new sys"},  # no prompt_template
            changed_by="auto",
            reason="test",
        )

        assert skill.system_prompt == "new sys"
        assert skill.prompt_template == "old tmpl"

    def test_update_prompt_partial_template_only(self):
        """_update_prompt() with only prompt_template update keeps old system prompt."""
        row = _skill_row(sys_prompt="old sys", tmpl="old tmpl")
        db = _make_mock_db(skill_row=row)
        backend = _make_mock_backend()

        skill = Skill("test", db, backend)
        skill._update_prompt(
            {"prompt_template": "new tmpl"},
            changed_by="auto",
            reason="test",
        )

        assert skill.system_prompt == "old sys"
        assert skill.prompt_template == "new tmpl"

    def test_update_prompt_empty_values_ignored(self):
        """_update_prompt() ignores empty string updates."""
        row = _skill_row(sys_prompt="sys", tmpl="tmpl")
        db = _make_mock_db(skill_row=row)
        backend = _make_mock_backend()

        skill = Skill("test", db, backend)
        skill._update_prompt(
            {"system_prompt": "", "prompt_template": ""},
            changed_by="auto", reason="test",
        )
        # Empty strings are falsy, so prompts should remain unchanged
        assert skill.system_prompt == "sys"
        assert skill.prompt_template == "tmpl"


# ── rollback() ────────────────────────────────────────────────────────


class TestSkillRollback:
    def test_rollback_success(self):
        """rollback() finds the target version and restores it."""
        row = _skill_row(version=3)
        db = _make_mock_db(skill_row=row)
        db.get_skill_versions.return_value = [
            {"version": 2, "system_prompt": "v2 sys", "prompt_template": "v2 tmpl"},
            {"version": 1, "system_prompt": "v1 sys", "prompt_template": "v1 tmpl"},
        ]
        backend = _make_mock_backend()

        skill = Skill("test", db, backend)
        result = skill.rollback(target_version=1)

        assert result is True
        assert skill.system_prompt == "v1 sys"
        assert skill.prompt_template == "v1 tmpl"
        # Should have called snapshot before rollback
        db.snapshot_skill_version.assert_called_once()
        db.update_skill_prompt.assert_called_once_with("test", "v1 sys", "v1 tmpl")
        assert skill.version == 4  # was 3, +1

    def test_rollback_version_not_found(self):
        """rollback() returns False when target version doesn't exist."""
        row = _skill_row(version=2)
        db = _make_mock_db(skill_row=row)
        db.get_skill_versions.return_value = [
            {"version": 1, "system_prompt": "v1 sys", "prompt_template": "v1 tmpl"},
        ]
        backend = _make_mock_backend()

        skill = Skill("test", db, backend)
        result = skill.rollback(target_version=99)

        assert result is False
        db.snapshot_skill_version.assert_not_called()

    def test_rollback_with_no_versions(self):
        """rollback() returns False when no version snapshots exist."""
        row = _skill_row(version=1)
        db = _make_mock_db(skill_row=row)
        db.get_skill_versions.return_value = []
        backend = _make_mock_backend()

        skill = Skill("test", db, backend)
        result = skill.rollback(target_version=1)

        assert result is False


# ── _parse_response() ─────────────────────────────────────────────────


class TestSkillParseResponse:
    def _make_skill(self):
        """Create a Skill with mocked DB and backend."""
        row = _skill_row()
        db = _make_mock_db(skill_row=row)
        backend = _make_mock_backend()
        return Skill("test", db, backend)

    def test_parse_valid_json(self):
        """_parse_response parses valid JSON string."""
        skill = self._make_skill()
        result = skill._parse_response('{"key": "value", "num": 42}')
        assert result == {"key": "value", "num": 42}

    def test_parse_invalid_json_returns_raw(self):
        """_parse_response returns {'raw_response': ...} for invalid JSON."""
        skill = self._make_skill()
        result = skill._parse_response("This is not JSON")
        assert "raw_response" in result
        assert "This is not JSON" in result["raw_response"]

    def test_parse_json_in_markdown_fences(self):
        """_parse_response extracts JSON from ```json ... ``` fences."""
        skill = self._make_skill()
        response = 'Here is the result:\n```json\n{"score": 0.85}\n```\nDone.'
        result = skill._parse_response(response)
        assert result == {"score": 0.85}

    def test_parse_none_returns_raw(self):
        """_parse_response handles None gracefully."""
        skill = self._make_skill()
        result = skill._parse_response(None)
        assert "raw_response" in result

    def test_parse_empty_string_returns_raw(self):
        """_parse_response handles empty string."""
        skill = self._make_skill()
        result = skill._parse_response("")
        assert "raw_response" in result

    def test_parse_nested_json(self):
        """_parse_response handles nested JSON structures."""
        skill = self._make_skill()
        nested = '{"queries": [{"q": "test", "score": 0.9}], "count": 1}'
        result = skill._parse_response(nested)
        assert result["count"] == 1
        assert result["queries"][0]["q"] == "test"

    def test_parse_json_with_unicode(self):
        """_parse_response handles JSON with unicode characters."""
        skill = self._make_skill()
        result = skill._parse_response('{"title": "Python教程", "score": 0.8}')
        assert result["title"] == "Python教程"


# ── Abstract method defaults ──────────────────────────────────────────


class TestSkillAbstractDefaults:
    def test_output_schema_default(self):
        """Default _output_schema returns a basic object schema."""
        row = _skill_row()
        db = _make_mock_db(skill_row=row)
        backend = _make_mock_backend()
        skill = Skill("test", db, backend)
        assert skill._output_schema() == {"type": "object"}

    def test_build_reflection_prompt_default(self):
        """Default _build_reflection_prompt returns empty string."""
        row = _skill_row()
        db = _make_mock_db(skill_row=row)
        backend = _make_mock_backend()
        skill = Skill("test", db, backend)
        assert skill._build_reflection_prompt([]) == ""

    def test_parse_reflection_default(self):
        """Default _parse_reflection returns None."""
        row = _skill_row()
        db = _make_mock_db(skill_row=row)
        backend = _make_mock_backend()
        skill = Skill("test", db, backend)
        assert skill._parse_reflection({"some": "data"}) is None
