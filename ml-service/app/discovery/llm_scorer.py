"""
LLM-based relevance scoring and candidate evaluation.

Scores how well a YouTube video matches a Bilibili trending keyword.
Supports Ollama (local), OpenAI, and Anthropic backends via LLMBackend.
"""
import logging
import math
from typing import Optional

from ..llm.backend import LLMBackend, create_backend
from .models import (
    CandidateEvaluation,
    RelevanceResult,
    TranslatedKeyword,
    TranslatedTitle,
    YouTubeCandidate,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a video content analyst specializing in cross-platform content. "
    "You help find English YouTube videos that can be translated and transported "
    "to Bilibili to capitalize on Chinese trending topics. "
    "Respond in the exact JSON format requested."
)

TRANSLATE_PROMPT_TEMPLATE = """\
A Chinese keyword is trending on Bilibili (Chinese video platform): "{keyword}"

Generate 2-3 English search queries to find relevant YouTube videos on this topic.
The goal is to find English-language YouTube videos that could be translated and \
re-uploaded to Bilibili to ride this trend.

Rules:
- Queries must be in English
- Focus on the underlying topic, not Chinese-specific names/events
- Make queries specific enough to find relevant content, but broad enough to get results
- If the keyword is about a Chinese-only event/person with no English equivalent, \
generate queries about the broader topic instead

Respond with JSON:
{{
  "english_queries": ["<query1>", "<query2>"],
  "topic_summary": "<brief English description of what this keyword is about>"
}}"""

TRANSLATE_TITLE_PROMPT_TEMPLATE = """\
Translate the following English YouTube video title into Chinese for a Bilibili audience.

Title: "{title}"

Rules:
- Translate naturally for a Chinese audience
- Keep proper nouns (names, brands, places) in their commonly used Chinese form or keep them in English
- Stay concise — Bilibili titles should be short and catchy
- If the title is already in Chinese, return it as-is
- Do NOT add extra commentary or explanation

Respond with JSON:
{{"chinese_title": "<translated title>"}}"""

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

EVALUATE_CANDIDATE_PROMPT = """\
Evaluate this YouTube video for transport to Bilibili.

## Candidate Video
- Title: {title}
- Channel: {channel}
- YouTube Views: {yt_views:,}
- YouTube Likes: {yt_likes:,}
- Duration: {duration}

## Neural Network Prediction (statistical model)
{nn_prediction_text}

## Similar Past Transports (from our database)
{vectorstore_text}

## Bilibili Novelty Check
{novelty_text}

## Task
Make a final prediction for this video's Bilibili performance.
Use the neural network prediction as a statistical reference point.
Use similar past transports to calibrate your estimate.
Consider the novelty check — if similar content already exists, lower your estimate.

Predicted view ranges for reference:
- log1p(1,000) = 6.9 (failed)
- log1p(10,000) = 9.2 (standard)
- log1p(50,000) = 10.8 (successful)
- log1p(100,000) = 11.5 (successful)
- log1p(1,000,000) = 13.8 (viral)

Respond with JSON:
{{
  "predicted_log_views": <float 6-14>,
  "predicted_views": <int>,
  "confidence": <float 0.0-1.0>,
  "label": "<failed|standard|successful|viral>",
  "reasoning": "<1-2 sentence explanation>"
}}"""


def _format_duration(seconds: int) -> str:
    """Format seconds to human-readable duration."""
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    if h:
        return f"{h}h{m}m{s}s"
    return f"{m}m{s}s"


class LLMScorer:
    """Scores video-keyword relevance and evaluates candidates using an LLM."""

    def __init__(
        self,
        model: str = "qwen2.5:7b",
        backend: Optional[LLMBackend] = None,
        backend_type: str = "ollama",
    ):
        if backend is not None:
            self._backend = backend
        else:
            self._backend = create_backend(backend_type=backend_type, model=model)

    def translate_keyword(self, keyword: str) -> Optional[TranslatedKeyword]:
        """Translate a Chinese trending keyword into English YouTube search queries."""
        prompt = TRANSLATE_PROMPT_TEMPLATE.format(keyword=keyword)

        try:
            content = self._backend.chat(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                json_schema=TranslatedKeyword.model_json_schema(),
            )

            result = TranslatedKeyword.model_validate_json(content)
            result.english_queries = [q.strip() for q in result.english_queries if q.strip()]

            if not result.english_queries:
                logger.warning("LLM returned no queries for '%s'", keyword)
                return None

            logger.info(
                "Translated '%s' -> %s (%s)",
                keyword, result.english_queries, result.topic_summary,
            )
            return result

        except Exception as e:
            logger.error("Keyword translation failed for '%s': %s", keyword, e)
            return None

    def translate_title(self, english_title: str) -> Optional[TranslatedTitle]:
        """Translate an English video title into Chinese for Bilibili."""
        prompt = TRANSLATE_TITLE_PROMPT_TEMPLATE.format(title=english_title)

        try:
            content = self._backend.chat(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                json_schema=TranslatedTitle.model_json_schema(),
            )

            result = TranslatedTitle.model_validate_json(content)

            if not result.chinese_title.strip():
                logger.warning("LLM returned empty title for '%s'", english_title)
                return None

            logger.info(
                "Translated title: '%s' -> '%s'",
                english_title, result.chinese_title,
            )
            return result

        except Exception as e:
            logger.error("Title translation failed for '%s': %s", english_title, e)
            return None

    def score_relevance(
        self, keyword: str, video: YouTubeCandidate
    ) -> Optional[RelevanceResult]:
        """Score how relevant a YouTube video is to a Bilibili keyword."""
        prompt = SCORE_PROMPT_TEMPLATE.format(
            keyword=keyword,
            title=video.title,
            channel=video.channel_title,
            description=video.description[:500],
            views=video.views,
            duration=_format_duration(video.duration_seconds),
        )

        try:
            content = self._backend.chat(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                json_schema=RelevanceResult.model_json_schema(),
            )

            result = RelevanceResult.model_validate_json(content)
            result.relevance_score = max(0.0, min(1.0, result.relevance_score))
            result.is_relevant = result.relevance_score >= 0.5
            return result

        except Exception as e:
            logger.error(
                "LLM scoring failed for '%s' / '%s': %s",
                keyword, video.title[:50], e,
            )
            return None

    def evaluate_candidate(
        self,
        candidate: YouTubeCandidate,
        nn_prediction: Optional[float],
        vectorstore_examples: list[dict],
        novelty_info: dict,
    ) -> Optional[CandidateEvaluation]:
        """Final LLM evaluation of a candidate video for transport.

        Args:
            candidate: The YouTube video candidate.
            nn_prediction: Neural predictor log_views prediction (or None).
            vectorstore_examples: Similar past transports from VectorStore.
            novelty_info: Dict with novelty check results.

        Returns:
            CandidateEvaluation or None on failure.
        """
        # Format neural prediction text
        if nn_prediction is not None:
            nn_views = math.expm1(nn_prediction)
            nn_text = (
                f"Neural model predicts log_views={nn_prediction:.2f} "
                f"(~{nn_views:,.0f} views)"
            )
        else:
            nn_text = "Neural model prediction unavailable."

        # Format VectorStore examples
        if vectorstore_examples:
            vs_lines = []
            for ex in vectorstore_examples[:5]:
                ex_views = math.expm1(ex["log_views"])
                vs_lines.append(
                    f"- \"{ex.get('bvid', 'unknown')}\" "
                    f"(similarity={ex.get('similarity', 0):.2f}) — "
                    f"{ex_views:,.0f} views (log={ex['log_views']:.2f})"
                )
            vs_text = "\n".join(vs_lines)
        else:
            vs_text = "No similar past transports found."

        # Format novelty info
        novelty_score = novelty_info.get("novelty_score", 1.0)
        similar_count = novelty_info.get("similar_count", 0)
        novelty_text = (
            f"Novelty score: {novelty_score:.2f} "
            f"({similar_count} similar videos found on Bilibili)"
        )
        if novelty_info.get("top_similar"):
            for sv in novelty_info["top_similar"][:3]:
                novelty_text += f"\n- \"{sv['title'][:50]}\" — {sv['views']:,} views"

        prompt = EVALUATE_CANDIDATE_PROMPT.format(
            title=candidate.title,
            channel=candidate.channel_title,
            yt_views=candidate.views,
            yt_likes=candidate.likes,
            duration=_format_duration(candidate.duration_seconds),
            nn_prediction_text=nn_text,
            vectorstore_text=vs_text,
            novelty_text=novelty_text,
        )

        try:
            content = self._backend.chat(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                json_schema=CandidateEvaluation.model_json_schema(),
            )

            result = CandidateEvaluation.model_validate_json(content)
            result.confidence = max(0.0, min(1.0, result.confidence))
            result.predicted_log_views = max(0.0, min(16.0, result.predicted_log_views))
            result.predicted_views = max(0, result.predicted_views)

            logger.info(
                "Evaluated '%s': %s (log_views=%.2f, confidence=%.2f)",
                candidate.title[:40], result.label,
                result.predicted_log_views, result.confidence,
            )
            return result

        except Exception as e:
            logger.error("Candidate evaluation failed for '%s': %s",
                         candidate.title[:40], e)
            return None
