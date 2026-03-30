"""MarketAnalysis skill — judges Bilibili market saturation and opportunity.

Evolves its saturation criteria through outcome reflection (Loop 2).
"""
import json
import logging
from typing import Any, Dict, List, Optional

from .base import Skill

logger = logging.getLogger(__name__)

DEFAULT_CRITERIA = (
    "- >5 videos with >10K views in the last 30 days = likely saturated\n"
    "- If top videos are >6 months old, there's a freshness gap\n"
    "- Low-production-quality existing videos = quality gap opportunity"
)


class MarketAnalysisSkill(Skill):
    """Assesses Bilibili market saturation and opportunity for content niches."""

    def __init__(self, name: str = "market_analysis", db=None, backend=None):
        self.learned_criteria = DEFAULT_CRITERIA
        super().__init__(name, db, backend)

    def _default_system_prompt(self) -> str:
        return (
            "You are a Bilibili market analyst. You assess whether a content niche is "
            "saturated or has opportunity for new video transports.\n\n"
            "Key criteria you've learned:\n"
            "{learned_criteria}\n\n"
            "Respond in the exact JSON format requested."
        )

    def _default_prompt_template(self) -> str:
        return (
            "Analyze the Bilibili market for this topic.\n\n"
            '## Search Query: "{bilibili_check}"\n'
            "## Results Summary:\n"
            "- Total videos found: {total}\n"
            "- Videos with >10K views: {high_view_count}\n"
            "- Videos from last 30 days: {recent_count}\n"
            "- View range: {min_views:,} - {max_views:,}\n\n"
            "## Top Existing Videos:\n"
            "{top_videos_with_dates}\n\n"
            "## Task\n"
            "Assess the market opportunity for a new transport in this niche.\n\n"
            "Respond with JSON:\n"
            '{{\n'
            '  "is_saturated": false,\n'
            '  "opportunity_score": 0.5,\n'
            '  "quality_gap": "medium",\n'
            '  "freshness_gap": "medium",\n'
            '  "reasoning": "...",\n'
            '  "suggested_angle": "..."\n'
            '}}'
        )

    def _output_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "is_saturated": {"type": "boolean"},
                "opportunity_score": {"type": "number"},
                "quality_gap": {"type": "string"},
                "freshness_gap": {"type": "string"},
                "reasoning": {"type": "string"},
                "suggested_angle": {"type": "string"},
            },
            "required": ["is_saturated", "opportunity_score"],
        }

    def execute(self, context: dict) -> dict:
        """Assess market opportunity for a Bilibili content niche."""
        rendered_system = self.system_prompt.format(
            learned_criteria=self.learned_criteria,
        )

        prompt = self.prompt_template.format(**context)

        response = self.backend.chat(
            messages=[
                {"role": "system", "content": rendered_system},
                {"role": "user", "content": prompt},
            ],
            json_schema=self._output_schema(),
        )
        result = self._parse_response(response)

        if "opportunity_score" in result:
            result["opportunity_score"] = max(0.0, min(1.0, float(result["opportunity_score"])))

        return result

    def reflect_on_outcomes(self, outcomes: list) -> Optional[dict]:
        """Loop 2 reflection: validate past saturation judgments against outcomes."""
        if not outcomes:
            return None

        judgment_list = self._format_judgment_outcomes(outcomes)

        reflection_prompt = (
            "You are reviewing your past market saturation judgments against actual outcomes.\n\n"
            "## Your Current Saturation Criteria\n"
            f"{self.learned_criteria}\n\n"
            "## Judgment Outcomes (our transported videos only)\n"
            f"{judgment_list}\n\n"
            "## Task\n"
            "1. Which saturation judgments were wrong? Why?\n"
            "2. Are your thresholds too strict or too lenient?\n"
            "3. Which signals best predict actual opportunity?\n\n"
            "Update your criteria.\n\n"
            "Respond with JSON:\n"
            '{\n'
            '  "updated_criteria": "...",\n'
            '  "threshold_adjustments": "...",\n'
            '  "analysis": "..."\n'
            '}'
        )

        response = self.backend.chat(
            messages=[
                {"role": "system", "content": "You are analyzing market judgments. Respond in JSON."},
                {"role": "user", "content": reflection_prompt},
            ],
        )
        result = self._parse_response(response)

        if result.get("updated_criteria"):
            criteria = result["updated_criteria"]
            if isinstance(criteria, list):
                criteria = "\n".join(str(c) for c in criteria)
            elif not isinstance(criteria, str):
                criteria = str(criteria)
            self.learned_criteria = criteria
            new_system = self._default_system_prompt().format(
                learned_criteria=self.learned_criteria,
            )
            self._update_prompt(
                {"system_prompt": new_system},
                changed_by="outcome_reflection",
                reason=result.get("analysis", "Market reflection update"),
            )

        return result

    @staticmethod
    def _format_judgment_outcomes(outcomes: list) -> str:
        if not outcomes:
            return "(no outcomes)"
        lines = []
        for o in outcomes:
            bili_views = o.get("bilibili_views") or 0
            novelty = o.get("bilibili_novelty_score") or 0
            query = o.get("bilibili_check") or "?"
            outcome = o.get("outcome") or "?"
            lines.append(
                f"  [{outcome.upper()}] Query \"{query}\": "
                f"novelty_score={novelty:.2f} -> {bili_views:,} Bilibili views"
            )
        return "\n".join(lines)

    def _build_reflection_prompt(self, outcomes: list) -> str:
        return ""

    def _parse_reflection(self, result: dict) -> Optional[dict]:
        return None
