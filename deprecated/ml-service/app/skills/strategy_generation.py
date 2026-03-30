"""StrategyGeneration skill — generates YouTube search queries and evolves strategies.

Owns two kinds of knowledge that evolve independently:
- YouTube search principles (updated by Loop 1 yield reflection, every run)
- Bilibili audience principles (updated by Loop 2 outcome reflection, when data arrives)
"""
import json
import logging
from typing import Any, Dict, List, Optional

from .base import Skill

logger = logging.getLogger(__name__)

DEFAULT_YOUTUBE_PRINCIPLES = (
    "- Specific queries outperform generic ones\n"
    "- Adding reaction words ('honest review', 'first time trying') finds more engaging content\n"
    "- Filter by recent uploads (<3 months) to avoid stale content\n"
    "- YouTube category 22 (People & Blogs) has high transport potential\n"
    "- Videos with >50K views have higher quality on average"
)

DEFAULT_BILIBILI_PRINCIPLES = (
    "- Food and culture content performs well\n"
    "- Videos under 15 minutes transport better than longer ones\n"
    "- Foreign appreciation of Chinese brands has high success rate\n"
    "- Avoid topics with >3 recent Bilibili transports in the last 2 weeks"
)


class StrategyGenerationSkill(Skill):
    """Generates YouTube search queries and manages discovery strategies."""

    def __init__(self, name: str = "strategy_generation", db=None, backend=None):
        self.youtube_principles = DEFAULT_YOUTUBE_PRINCIPLES
        self.bilibili_principles = DEFAULT_BILIBILI_PRINCIPLES
        super().__init__(name, db, backend)
        self._extract_principles()

    def _extract_principles(self) -> None:
        """Extract youtube/bilibili principles from system prompt."""
        if "{youtube_principles}" in self._default_system_prompt():
            # Principles are embedded in the actual system_prompt at render time
            # Parse them from stored system_prompt if it contains the marker
            pass

    def _default_system_prompt(self) -> str:
        return (
            "You are an expert at finding specific YouTube videos that will succeed when "
            "transported to Bilibili. Your primary job is crafting effective YouTube search "
            "queries -- the right query finds the right video.\n\n"
            "CRITICAL RULE: ALL YouTube search queries MUST be in English. YouTube's search "
            "engine works best with English queries, and the target videos are English-language "
            "content by foreign creators. Never generate Chinese/non-English queries for YouTube. "
            "The bilibili_check field should be in Chinese (for checking Bilibili saturation).\n\n"
            "QUERY FORMAT RULE: The query field must be a natural YouTube search string that a "
            "human would type. NEVER include strategy names, internal labels, or years unless the "
            "year is genuinely relevant. BAD: 'global_trending_chinese_angle AI news'. "
            "GOOD: 'why AI is taking over and no one is ready'. The strategy_name field is separate "
            "— do not embed it in the query.\n\n"
            "YouTube search principles you've learned:\n"
            "{youtube_principles}\n\n"
            "Bilibili audience principles you've learned:\n"
            "{bilibili_principles}\n\n"
            "Respond in the exact JSON format requested."
        )

    def _default_prompt_template(self) -> str:
        return (
            "Find YouTube videos suitable for transport to Bilibili.\n\n"
            "## Active Strategies\n"
            "{strategies_with_full_context}\n\n"
            "## Recent Discovery Outcomes (last 30 days)\n"
            "{recent_outcomes_with_youtube_context}\n\n"
            "## Current Bilibili Hot Words (demand signals)\n"
            "{hot_words}\n\n"
            "## Task\n"
            "Generate 10-20 YouTube search queries. Focus on FINDING good videos:\n"
            "1. ALL queries MUST be in English (e.g. 'Chinese motorcycle brand review', "
            "NOT '国外博主介绍国产摩托车品牌')\n"
            "2. Use query patterns that have historically returned good results\n"
            "3. Reference specific channels known to produce transportable content\n"
            "4. Be specific enough to find relevant content, but not so narrow\n"
            "5. Combine proven strategy angles with current demand signals\n"
            "6. Avoid query patterns that previously returned poor results\n"
            "7. NEVER put strategy names or internal labels in the query field. "
            "BAD: 'chinese_brand_foreign_review Huawei' or 'challenge_experiment building'. "
            "GOOD: 'Huawei P70 honest review foreigner' or 'I built an impossible bridge challenge'\n\n"
            "Respond with JSON:\n"
            '{{\n'
            '  "queries": [\n'
            '    {{"query": "...", "strategy_name": "...", "bilibili_check": "...", '
            '"target_channels": [], "reasoning": "..."}}\n'
            '  ],\n'
            '  "new_strategy_proposals": [\n'
            '    {{"name": "...", "description": "...", "youtube_tactics": "...", '
            '"example_queries": [], "target_channels": [], "bilibili_check": "...", '
            '"reasoning": "..."}}\n'
            '  ],\n'
            '  "retire_suggestions": []\n'
            '}}'
        )

    def _output_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "queries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "strategy_name": {"type": "string"},
                            "bilibili_check": {"type": "string"},
                            "target_channels": {"type": "array", "items": {"type": "string"}},
                            "reasoning": {"type": "string"},
                        },
                        "required": ["query", "strategy_name"],
                    },
                },
                "new_strategy_proposals": {"type": "array"},
                "retire_suggestions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["queries"],
        }

    def execute(self, context: dict) -> dict:
        """Generate YouTube search queries.

        Expected context keys:
            strategies_with_full_context: str
            recent_outcomes_with_youtube_context: str
            hot_words: str
        """
        # Render system prompt with current principles (if template has placeholders)
        if "{youtube_principles}" in self.system_prompt:
            rendered_system = self.system_prompt.format(
                youtube_principles=self.youtube_principles,
                bilibili_principles=self.bilibili_principles,
            )
        else:
            # System prompt was already rendered (e.g. after reflection saved it)
            rendered_system = self.system_prompt

        prompt = self.prompt_template.format(**context)

        response = self.backend.chat(
            messages=[
                {"role": "system", "content": rendered_system},
                {"role": "user", "content": prompt},
            ],
            json_schema=self._output_schema(),
            temperature=0.9,
        )
        return self._parse_response(response)

    def reflect_on_yield(self, yield_data: list, strategy_stats: list) -> Optional[dict]:
        """Loop 1 reflection: analyze query yield and update YouTube principles.

        Args:
            yield_data: Recent strategy run results.
            strategy_stats: Per-strategy yield statistics.

        Returns:
            Parsed reflection result, or None.
        """
        if not yield_data:
            return None

        query_yield_report = self.format_yield_report(yield_data)
        strategy_yield_stats = self.format_strategy_stats(strategy_stats)

        reflection_prompt = (
            "You are reviewing your YouTube search performance from the latest discovery run.\n\n"
            "## Your Current YouTube Search Principles\n"
            f"{self.youtube_principles}\n\n"
            "## This Run's Query Results\n"
            f"{query_yield_report}\n\n"
            "## Strategy Yield Rates (cumulative)\n"
            f"{strategy_yield_stats}\n\n"
            "## Task\n"
            "Analyze YouTube search effectiveness ONLY:\n"
            "1. Which query patterns found good videos? Which returned nothing?\n"
            "2. Why did empty/low-quality queries fail?\n"
            "3. Any new channels discovered worth following?\n"
            "4. Should any strategies be retired based on low yield?\n\n"
            "Update your YouTube search principles.\n\n"
            "Respond with JSON:\n"
            '{\n'
            '  "updated_youtube_principles": "...",\n'
            '  "new_strategies": [],\n'
            '  "channels_to_follow": [],\n'
            '  "retire": [],\n'
            '  "analysis": "..."\n'
            '}'
        )

        response = self.backend.chat(
            messages=[
                {"role": "system", "content": "You are analyzing search performance. Respond in JSON."},
                {"role": "user", "content": reflection_prompt},
            ],
            temperature=0.3,
        )
        result = self._parse_response(response)

        if result.get("updated_youtube_principles"):
            principles = result["updated_youtube_principles"]
            # LLM may return a list or dict instead of a string — normalize
            if isinstance(principles, list):
                principles = "\n".join(str(p) for p in principles)
            elif not isinstance(principles, str):
                principles = str(principles)
            self.youtube_principles = principles
            # Save the template (with placeholders) so execute() can still format it
            self._update_prompt(
                {"system_prompt": self._default_system_prompt()},
                changed_by="yield_reflection",
                reason=result.get("analysis", "Yield reflection update"),
            )

        return result

    def reflect_on_outcomes(self, outcomes: list) -> Optional[dict]:
        """Loop 2 reflection: analyze Bilibili outcomes and update audience principles.

        Args:
            outcomes: List of outcome dicts with transport results.

        Returns:
            Parsed reflection result, or None.
        """
        if not outcomes:
            return None

        outcome_list = self.format_outcomes(outcomes)

        reflection_prompt = (
            "You are reviewing the Bilibili performance of videos YOU recommended.\n\n"
            "## Your Current Bilibili Audience Principles\n"
            f"{self.bilibili_principles}\n\n"
            "## Our Transport Outcomes (newly recorded)\n"
            f"{outcome_list}\n\n"
            "## Task\n"
            "Analyze what content succeeds on Bilibili after transport:\n"
            "1. What patterns predict Bilibili success?\n"
            "2. Does YouTube view count, duration, category matter most?\n"
            "3. Content types that consistently fail despite looking promising?\n\n"
            "Update your Bilibili audience principles.\n\n"
            "Respond with JSON:\n"
            '{\n'
            '  "updated_bilibili_principles": "...",\n'
            '  "scoring_insights": "...",\n'
            '  "analysis": "..."\n'
            '}'
        )

        response = self.backend.chat(
            messages=[
                {"role": "system", "content": "You are analyzing transport outcomes. Respond in JSON."},
                {"role": "user", "content": reflection_prompt},
            ],
            temperature=0.3,
        )
        result = self._parse_response(response)

        if result.get("updated_bilibili_principles"):
            principles = result["updated_bilibili_principles"]
            if isinstance(principles, list):
                principles = "\n".join(str(p) for p in principles)
            elif not isinstance(principles, str):
                principles = str(principles)
            self.bilibili_principles = principles
            # Save the template (with placeholders) so execute() can still format it
            self._update_prompt(
                {"system_prompt": self._default_system_prompt()},
                changed_by="outcome_reflection",
                reason=result.get("analysis", "Outcome reflection update"),
            )

        return result

    # ── Formatting helpers ───────────────────────────────────────────────

    @staticmethod
    def format_strategies_context(strategies: list) -> str:
        """Format strategy list for prompt context."""
        if not strategies:
            return "(no active strategies)"
        lines = []
        for s in strategies:
            name = s.get("name", "?")
            desc = s.get("description", "")
            yield_rate = s.get("yield_rate", 0)
            queries = s.get("example_queries", "[]")
            check = s.get("bilibili_check", "")
            lines.append(
                f"[{name}] (yield: {yield_rate:.0%})\n"
                f"  What to find: {desc}\n"
                f"  Query style examples: {queries}\n"
                f"  Bilibili saturation keyword: {check}"
            )
        return "\n\n".join(lines)

    @staticmethod
    def format_recent_outcomes(outcomes: list) -> str:
        """Format recent outcomes for prompt context."""
        if not outcomes:
            return "(no recent outcomes)"
        lines = []
        for o in outcomes:
            status = o.get("outcome", "pending")
            query = o.get("query", "?")
            yt_title = o.get("youtube_title", "?")
            yt_views = o.get("youtube_views", 0)
            bili_views = o.get("bilibili_views")
            prefix = "[success]" if status == "success" else "[failure]" if status == "failure" else "[pending]"
            line = f"  {prefix} query \"{query}\" -> \"{yt_title}\" (YT: {yt_views:,})"
            if bili_views is not None:
                line += f" -> {bili_views:,} Bilibili views"
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def format_hot_words(hot_words: list) -> str:
        """Format hot words/trending keywords for prompt context."""
        if not hot_words:
            return "(no hot words available)"
        return "\n".join(f"  - {hw}" for hw in hot_words[:20])

    @staticmethod
    def format_yield_report(yield_data: list) -> str:
        """Format yield data for reflection prompt."""
        if not yield_data:
            return "(no data)"
        lines = []
        for r in yield_data:
            name = r.get("strategy_name", "?")
            query = r.get("query", "?")
            count = r.get("query_result_count", 0)
            success = r.get("yield_success", 0)
            yt_title = r.get("youtube_title", "")
            if success:
                lines.append(f"  [YIELD] \"{query}\" ({name}) -> {count} results, best: \"{yt_title}\"")
            elif count and count > 0:
                lines.append(f"  [LOW-Q] \"{query}\" ({name}) -> {count} results, low quality")
            else:
                lines.append(f"  [EMPTY] \"{query}\" ({name}) -> 0 results")
        return "\n".join(lines)

    @staticmethod
    def format_strategy_stats(stats: list) -> str:
        """Format strategy yield stats for reflection prompt."""
        if not stats:
            return "(no stats)"
        lines = []
        for s in stats:
            name = s.get("name", "?")
            total = s.get("total_queries", 0)
            yielded = s.get("yielded_queries", 0)
            rate = s.get("yield_rate", 0)
            lines.append(f"  {name}: yield {rate:.0%} ({yielded}/{total})")
        return "\n".join(lines)

    @staticmethod
    def format_outcomes(outcomes: list) -> str:
        """Format transport outcomes for Loop 2 reflection."""
        if not outcomes:
            return "(no outcomes)"
        lines = []
        for o in outcomes:
            status = o.get("outcome", "?")
            yt_title = o.get("youtube_title", "?")
            yt_views = o.get("youtube_views", 0)
            bili_views = o.get("bilibili_views", 0)
            strategy = o.get("strategy_name", "?")
            query = o.get("query", "?")
            prefix = "[SUCCESS]" if status == "success" else "[FAILURE]"
            lines.append(
                f"  {prefix} \"{yt_title}\" (YT: {yt_views:,})\n"
                f"    -> found via: strategy={strategy}, query=\"{query}\"\n"
                f"    -> Bilibili: {bili_views:,} views"
            )
        return "\n".join(lines)

    def _build_reflection_prompt(self, outcomes: list) -> str:
        """Generic reflection (delegates to specific reflection methods)."""
        return ""

    def _parse_reflection(self, result: dict) -> Optional[dict]:
        return None
