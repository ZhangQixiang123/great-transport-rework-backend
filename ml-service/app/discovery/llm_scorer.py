"""
LLM-based relevance scoring using Ollama + Qwen.

Scores how well a YouTube video matches a Bilibili trending keyword.
"""
import logging
from typing import Optional

import ollama

from .models import RelevanceResult, YouTubeCandidate

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a video content analyst. You evaluate how relevant a YouTube video "
    "is to a Bilibili trending keyword. Consider topic match, audience overlap, "
    "and whether transporting this video to Bilibili would capitalize on the trend. "
    "Respond in the exact JSON format requested."
)

SCORE_PROMPT_TEMPLATE = """\
Bilibili trending keyword: "{keyword}"

YouTube video:
- Title: {title}
- Channel: {channel}
- Description: {description}
- Views: {views:,}
- Duration: {duration}

Rate how relevant this YouTube video is to the Bilibili trending keyword.
Consider:
1. Does the video topic match the keyword?
2. Would Bilibili audiences searching this keyword want to watch this video?
3. Is the content suitable for transport (re-upload) to Bilibili?

Respond with JSON:
{{
  "relevance_score": <float 0.0-1.0>,
  "reasoning": "<1-2 sentence explanation>",
  "detected_topics": ["<topic1>", "<topic2>"],
  "is_relevant": <true if score >= 0.5>
}}"""


def _format_duration(seconds: int) -> str:
    """Format seconds to human-readable duration."""
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    if h:
        return f"{h}h{m}m{s}s"
    return f"{m}m{s}s"


class LLMScorer:
    """Scores video-keyword relevance using a local LLM via Ollama."""

    def __init__(self, model: str = "qwen2.5:7b"):
        self.model = model
        self._verify_connection()

    def _verify_connection(self):
        """Check that Ollama is running and model is available."""
        try:
            models = ollama.list()
            available = [m.model for m in models.models]
            if self.model not in available:
                # Try without tag
                base_name = self.model.split(":")[0]
                found = any(m.startswith(base_name) for m in available)
                if not found:
                    logger.warning(
                        "Model '%s' not found in Ollama. Available: %s. "
                        "Will attempt to pull on first use.",
                        self.model,
                        available,
                    )
        except Exception as e:
            logger.warning("Cannot connect to Ollama: %s", e)

    def score_relevance(
        self, keyword: str, video: YouTubeCandidate
    ) -> Optional[RelevanceResult]:
        """Score how relevant a YouTube video is to a Bilibili keyword.

        Args:
            keyword: The Bilibili trending keyword.
            video: The YouTube video candidate.

        Returns:
            RelevanceResult with score and reasoning, or None on failure.
        """
        prompt = SCORE_PROMPT_TEMPLATE.format(
            keyword=keyword,
            title=video.title,
            channel=video.channel_title,
            description=video.description[:500],
            views=video.views,
            duration=_format_duration(video.duration_seconds),
        )

        try:
            response = ollama.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                format=RelevanceResult.model_json_schema(),
            )

            content = response.message.content
            result = RelevanceResult.model_validate_json(content)

            # Clamp score to [0, 1]
            result.relevance_score = max(0.0, min(1.0, result.relevance_score))
            result.is_relevant = result.relevance_score >= 0.5

            return result

        except Exception as e:
            logger.error(
                "LLM scoring failed for '%s' / '%s': %s",
                keyword,
                video.title[:50],
                e,
            )
            return None
