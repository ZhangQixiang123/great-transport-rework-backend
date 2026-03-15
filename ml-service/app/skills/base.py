"""Skill base class — a self-improving LLM capability.

A skill bundles a mutable system prompt, prompt template, and output schema.
Prompts are stored in the database, versioned, and evolved by reflection.
"""
import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class Skill:
    """Base class for self-improving LLM skills."""

    def __init__(self, name: str, db, backend):
        """Initialize the skill.

        Args:
            name: Unique skill identifier (e.g. 'strategy_generation').
            db: Database instance (must be connected).
            backend: LLMBackend instance for chat calls.
        """
        self.name = name
        self.db = db
        self.backend = backend
        self.system_prompt: str = ""
        self.prompt_template: str = ""
        self.output_schema: dict = {}
        self.version: int = 1
        self._load_from_db()

    def _load_from_db(self) -> None:
        """Load skill state from DB, or seed defaults if not found."""
        row = self.db.get_skill(self.name)
        if row:
            self.system_prompt = row["system_prompt"]
            self.prompt_template = row["prompt_template"]
            self.output_schema = json.loads(row["output_schema"])
            self.version = row["version"]
        else:
            # Seed defaults from subclass
            self.system_prompt = self._default_system_prompt()
            self.prompt_template = self._default_prompt_template()
            self.output_schema = self._output_schema()
            self.db.upsert_skill(
                self.name, self.system_prompt, self.prompt_template,
                json.dumps(self.output_schema),
            )
            self.version = 1

    def execute(self, context: dict) -> dict:
        """Run the skill with context, return structured output.

        Args:
            context: Dict of template variables to format into prompt_template.

        Returns:
            Parsed JSON dict from LLM response.
        """
        prompt = self.prompt_template.format(**context)
        response = self.backend.chat(
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ],
            json_schema=self.output_schema,
        )
        return self._parse_response(response)

    def reflect(self, outcomes: list) -> Optional[dict]:
        """Analyze outcomes and potentially rewrite own prompt.

        Args:
            outcomes: List of outcome dicts to reflect on.

        Returns:
            Parsed reflection result, or None if reflection is skipped.
        """
        if not outcomes:
            return None
        reflection_prompt = self._build_reflection_prompt(outcomes)
        if not reflection_prompt:
            return None
        response = self.backend.chat(
            messages=[
                {"role": "system", "content": "You are analyzing past outcomes to improve your prompts."},
                {"role": "user", "content": reflection_prompt},
            ],
            json_schema=None,
        )
        result = self._parse_response(response)
        parsed = self._parse_reflection(result)
        if parsed:
            self._update_prompt(parsed, changed_by="reflection", reason=parsed.get("analysis", ""))
        return parsed

    def _update_prompt(self, updates: dict, changed_by: str, reason: str) -> None:
        """Version the current prompt and apply updates."""
        # Snapshot current version (ensure reason is a string for SQLite)
        reason_str = reason if isinstance(reason, str) else str(reason)
        self.db.snapshot_skill_version(
            self.name, changed_by=changed_by, reason=reason_str,
        )
        # Apply updates
        if "system_prompt" in updates and updates["system_prompt"]:
            self.system_prompt = updates["system_prompt"]
        if "prompt_template" in updates and updates["prompt_template"]:
            self.prompt_template = updates["prompt_template"]
        # Persist
        self.db.update_skill_prompt(
            self.name, self.system_prompt, self.prompt_template,
        )
        self.version += 1

    def rollback(self, target_version: int) -> bool:
        """Restore skill prompts from a previous version snapshot.

        Args:
            target_version: The version number to roll back to.

        Returns:
            True if rollback succeeded, False if version not found.
        """
        versions = self.db.get_skill_versions(self.name)
        for v in versions:
            if v["version"] == target_version:
                # Snapshot current before rollback
                self.db.snapshot_skill_version(
                    self.name, changed_by="rollback",
                    reason=f"Rolling back to version {target_version}",
                )
                self.system_prompt = v["system_prompt"]
                self.prompt_template = v["prompt_template"]
                self.db.update_skill_prompt(
                    self.name, self.system_prompt, self.prompt_template,
                )
                self.version += 1
                return True
        return False

    def _parse_response(self, response: str) -> dict:
        """Parse LLM response as JSON, with fallbacks for common LLM quirks."""
        text = str(response).strip()

        # Try direct parse first
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            pass

        # Try extracting from markdown fences
        if "```json" in text:
            try:
                start = text.index("```json") + 7
                end = text.index("```", start)
                return json.loads(text[start:end].strip())
            except (json.JSONDecodeError, ValueError):
                pass
        elif "```" in text:
            try:
                start = text.index("```") + 3
                end = text.index("```", start)
                return json.loads(text[start:end].strip())
            except (json.JSONDecodeError, ValueError):
                pass

        # Try fixing common LLM JSON issues: trailing commas, single quotes
        import re
        cleaned = text
        # Extract the outermost JSON object
        m = re.search(r'\{', cleaned)
        if m:
            depth = 0
            start_i = m.start()
            for i in range(start_i, len(cleaned)):
                if cleaned[i] == '{':
                    depth += 1
                elif cleaned[i] == '}':
                    depth -= 1
                    if depth == 0:
                        cleaned = cleaned[start_i:i + 1]
                        break
        # Remove trailing commas before } or ]
        cleaned = re.sub(r',\s*([}\]])', r'\1', cleaned)
        try:
            return json.loads(cleaned)
        except (json.JSONDecodeError, TypeError):
            pass

        logger.warning("Failed to parse skill response as JSON: %.200s", text)
        return {"raw_response": text}

    # ── Abstract methods for subclasses ──────────────────────────────────

    def _default_system_prompt(self) -> str:
        """Return default system prompt for this skill. Override in subclass."""
        return "You are a helpful assistant. Respond in JSON."

    def _default_prompt_template(self) -> str:
        """Return default prompt template. Override in subclass."""
        return "{input}"

    def _output_schema(self) -> dict:
        """Return JSON schema for structured output. Override in subclass."""
        return {"type": "object"}

    def _build_reflection_prompt(self, outcomes: list) -> str:
        """Build reflection prompt from outcomes. Override in subclass."""
        return ""

    def _parse_reflection(self, result: dict) -> Optional[dict]:
        """Parse reflection result and extract prompt updates. Override in subclass."""
        return None
