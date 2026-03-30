"""LLM-based video performance predictor.

Evaluates candidate videos using ML prediction as reference,
VectorStore examples for calibration, and novelty check context.
"""
import logging
import math
from typing import Optional

from ..llm.backend import LLMBackend, create_backend

logger = logging.getLogger(__name__)

PREDICTOR_SYSTEM_PROMPT = (
    "You are an expert analyst predicting how well YouTube videos will perform "
    "when transported (re-uploaded with Chinese subtitles) to Bilibili. "
    "You understand Chinese internet culture, Bilibili audience preferences, "
    "trending topics, and content virality factors. "
    "Respond in the exact JSON format requested."
)

PREDICT_PROMPT_TEMPLATE = """\
Predict how this YouTube video will perform if transported to Bilibili.

## Candidate Video
- Title: {title}
- Channel: {channel}
- YouTube Views: {yt_views:,}
- YouTube Likes: {yt_likes:,}
- YouTube Comments: {yt_comments:,}
- Duration: {duration}
- Category ID: {category_id}

## Neural Network Prediction (statistical reference)
{nn_prediction_text}

## Similar Past Transports (actual Bilibili performance data)
{vectorstore_text}

## Bilibili Novelty Check
{novelty_text}

## Task
Make a final prediction about this video's Bilibili performance.

Use the neural network prediction as a statistical baseline.
Use similar past transports to calibrate — if similar videos get ~50K views,
your estimate should be in that range unless there's a good reason to deviate.
Factor in the novelty check — saturated content will perform worse.

Predicted view ranges:
- log1p(1,000) = 6.9 → "failed"
- log1p(10,000) = 9.2 → "standard"
- log1p(50,000) = 10.8 → "successful"
- log1p(100,000) = 11.5 → "successful"
- log1p(1,000,000) = 13.8 → "viral"

Respond with JSON:
{{
  "predicted_log_views": <float>,
  "predicted_views": <int>,
  "confidence": <float 0.0-1.0>,
  "label": "<failed|standard|successful|viral>",
  "reasoning": "<1-2 sentence explanation>"
}}"""


def _format_duration(seconds: int) -> str:
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    if h:
        return f"{h}h{m}m{s}s"
    return f"{m}m{s}s"


class LLMPredictor:
    """Predicts video performance using an LLM with evidence context."""

    def __init__(
        self,
        backend: Optional[LLMBackend] = None,
        backend_type: str = "ollama",
        model: Optional[str] = None,
    ):
        if backend is not None:
            self._backend = backend
        else:
            self._backend = create_backend(backend_type=backend_type, model=model)

    def predict(
        self,
        title: str,
        channel: str,
        yt_views: int,
        yt_likes: int,
        yt_comments: int,
        duration_seconds: int,
        category_id: int,
        nn_prediction: Optional[float] = None,
        vectorstore_examples: Optional[list[dict]] = None,
        novelty_info: Optional[dict] = None,
    ) -> Optional[dict]:
        """Predict video performance using LLM with evidence context.

        Args:
            title: YouTube video title.
            channel: YouTube channel name.
            yt_views: YouTube view count.
            yt_likes: YouTube like count.
            yt_comments: YouTube comment count.
            duration_seconds: Video duration in seconds.
            category_id: YouTube category ID.
            nn_prediction: Neural predictor log_views (or None).
            vectorstore_examples: Similar past transports from VectorStore.
            novelty_info: Bilibili novelty check results.

        Returns:
            Dict with predicted_log_views, predicted_views, confidence,
            label, reasoning. Or None on failure.
        """
        # Format neural prediction
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
            for ex in (vectorstore_examples or [])[:5]:
                ex_views = math.expm1(ex["log_views"])
                vs_lines.append(
                    f"- Similarity={ex.get('similarity', 0):.2f} — "
                    f"{ex_views:,.0f} views (log={ex['log_views']:.2f})"
                )
            vs_text = "\n".join(vs_lines)
        else:
            vs_text = "No similar past transports found."

        # Format novelty info
        if novelty_info:
            novelty_score = novelty_info.get("novelty_score", 1.0)
            similar_count = novelty_info.get("similar_count", 0)
            novelty_text = (
                f"Novelty score: {novelty_score:.2f} "
                f"({similar_count} similar videos found on Bilibili)"
            )
        else:
            novelty_text = "No novelty check performed."

        prompt = PREDICT_PROMPT_TEMPLATE.format(
            title=title,
            channel=channel,
            yt_views=yt_views,
            yt_likes=yt_likes,
            yt_comments=yt_comments,
            duration=_format_duration(duration_seconds),
            category_id=category_id,
            nn_prediction_text=nn_text,
            vectorstore_text=vs_text,
            novelty_text=novelty_text,
        )

        try:
            from ..discovery.models import CandidateEvaluation

            content = self._backend.chat(
                messages=[
                    {"role": "system", "content": PREDICTOR_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                json_schema=CandidateEvaluation.model_json_schema(),
            )

            result = CandidateEvaluation.model_validate_json(content)

            # Clamp values
            result.confidence = max(0.0, min(1.0, result.confidence))
            result.predicted_log_views = max(0.0, min(16.0, result.predicted_log_views))
            result.predicted_views = max(0, result.predicted_views)

            logger.info(
                "LLM prediction for '%s': %s (log_views=%.2f, confidence=%.2f)",
                title[:40], result.label,
                result.predicted_log_views, result.confidence,
            )
            return result.model_dump()

        except Exception as e:
            logger.error("LLM prediction failed for '%s': %s", title[:40], e)
            return None
