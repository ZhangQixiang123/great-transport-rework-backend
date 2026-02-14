"""
Discovery pipeline orchestrator.

Combines Bilibili trending keywords, YouTube search, LLM relevance scoring,
and ML view prediction into ranked recommendations.
"""
import logging
import math
from datetime import datetime
from typing import Optional

import numpy as np

from ..db.database import CompetitorVideo, Database
from .llm_scorer import LLMScorer
from .models import Recommendation, YouTubeCandidate
from .trending import fetch_trending_keywords
from .youtube_search import search_youtube_videos

logger = logging.getLogger(__name__)


def _make_dummy_video(candidate: YouTubeCandidate) -> CompetitorVideo:
    """Create a CompetitorVideo from a YouTubeCandidate for ML prediction.

    The ranker expects a CompetitorVideo. We fill in what we can from
    the YouTube data and set Bilibili-specific fields to defaults.
    """
    return CompetitorVideo(
        bvid="",
        bilibili_uid="",
        title=candidate.title,
        description=candidate.description,
        duration=candidate.duration_seconds,
        views=0,
        likes=0,
        coins=0,
        favorites=0,
        shares=0,
        danmaku=0,
        comments=0,
        publish_time=None,
        collected_at=datetime.now(),
        youtube_source_id=candidate.video_id,
        label=None,
    )


def _make_yt_stats(candidate: YouTubeCandidate) -> dict:
    """Build a YouTube stats dict matching what extract_features_single expects."""
    return {
        "yt_views": candidate.views,
        "yt_likes": candidate.likes,
        "yt_comments": candidate.comments,
        "yt_duration_seconds": candidate.duration_seconds,
        "yt_category_id": candidate.category_id,
        "yt_tags": candidate.tags,
        "yt_published_at": candidate.published_at,
    }


class DiscoveryPipeline:
    """Orchestrates the full hot-words discovery pipeline."""

    def __init__(
        self,
        db: Database,
        model_dir: str = "models",
        llm_model: str = "qwen2.5:7b",
    ):
        self.db = db
        self.model_dir = model_dir
        self.scorer = LLMScorer(model=llm_model)

        # Lazy-load ranker (may not have a trained model yet)
        self._ranker = None

    def _get_ranker(self):
        """Load ranker model on first use."""
        if self._ranker is None:
            try:
                from ..models.ranker import RankerModel
                self._ranker = RankerModel.load_latest(self.model_dir)
                logger.info("Loaded ranker model from %s", self.model_dir)
            except FileNotFoundError:
                logger.warning(
                    "No trained model found in %s. "
                    "View predictions will be unavailable.",
                    self.model_dir,
                )
        return self._ranker

    async def run(
        self,
        max_keywords: int = 10,
        videos_per_keyword: int = 5,
    ) -> list[Recommendation]:
        """Run the full discovery pipeline.

        Steps:
            1. Fetch trending keywords from Bilibili
            2. For each keyword, search YouTube for candidates
            3. Score relevance with LLM
            4. Predict Bilibili views with ML model
            5. Compute combined score and rank
            6. Save results to DB

        Args:
            max_keywords: Max trending keywords to process.
            videos_per_keyword: Max YouTube videos per keyword.

        Returns:
            Ranked list of Recommendations.
        """
        # 1. Fetch trending keywords
        logger.info("Step 1: Fetching trending keywords...")
        keywords = await fetch_trending_keywords()
        keywords = keywords[:max_keywords]

        if not keywords:
            logger.warning("No trending keywords found")
            return []

        logger.info("Got %d keywords", len(keywords))

        # 2-4. Process each keyword
        all_recommendations: list[Recommendation] = []
        total_candidates = 0

        for kw in keywords:
            logger.info("Processing keyword: %s (heat=%d)", kw.keyword, kw.heat_score)

            # 2. Search YouTube
            candidates = search_youtube_videos(kw.keyword, max_results=videos_per_keyword)
            total_candidates += len(candidates)

            for candidate in candidates:
                # 3. Score relevance with LLM
                relevance = self.scorer.score_relevance(kw.keyword, candidate)
                if relevance is None:
                    continue
                if not relevance.is_relevant:
                    logger.debug(
                        "Skipping irrelevant: %s (score=%.2f)",
                        candidate.title[:40],
                        relevance.relevance_score,
                    )
                    continue

                # 4. Predict views with ML model
                pred_log_views = None
                pred_views = None
                pred_label = None

                ranker = self._get_ranker()
                if ranker is not None:
                    try:
                        dummy_video = _make_dummy_video(candidate)
                        yt_stats = _make_yt_stats(candidate)
                        prediction = ranker.predict_video(
                            dummy_video, yt_stats=yt_stats
                        )
                        pred_log_views = prediction["predicted_log_views"]
                        pred_views = prediction["predicted_views"]
                        pred_label = prediction["label"]
                    except Exception as e:
                        logger.warning("Prediction failed for %s: %s", candidate.video_id, e)

                # 5. Compute combined score
                combined = self._compute_combined_score(
                    heat_score=kw.heat_score,
                    relevance=relevance.relevance_score,
                    predicted_views=pred_views,
                )

                rec = Recommendation(
                    keyword=kw.keyword,
                    heat_score=kw.heat_score,
                    youtube_video_id=candidate.video_id,
                    youtube_title=candidate.title,
                    youtube_channel=candidate.channel_title,
                    youtube_views=candidate.views,
                    youtube_likes=candidate.likes,
                    youtube_duration_seconds=candidate.duration_seconds,
                    relevance_score=relevance.relevance_score,
                    relevance_reasoning=relevance.reasoning,
                    predicted_log_views=pred_log_views,
                    predicted_views=pred_views,
                    predicted_label=pred_label,
                    combined_score=combined,
                )
                all_recommendations.append(rec)

        # Sort by combined score descending
        all_recommendations.sort(key=lambda r: r.combined_score, reverse=True)

        # 6. Save results to DB
        self.db.ensure_discovery_tables()
        run_id = self.db.save_discovery_run(
            keywords_fetched=len(keywords),
            candidates_found=total_candidates,
            recommendations_count=len(all_recommendations),
        )
        self.db.save_recommendations(run_id, all_recommendations)

        logger.info(
            "Pipeline complete: %d keywords, %d candidates, %d recommendations",
            len(keywords),
            total_candidates,
            len(all_recommendations),
        )
        return all_recommendations

    def _compute_combined_score(
        self,
        heat_score: int,
        relevance: float,
        predicted_views: Optional[float],
    ) -> float:
        """Combine signals into a final ranking score.

        Each signal is normalized to [0, 1] range, then weighted:
          heat_weight=0.2, relevance_weight=0.4, views_weight=0.4

        Args:
            heat_score: Bilibili keyword heat score.
            relevance: LLM relevance score (already 0-1).
            predicted_views: Predicted Bilibili views (may be None).

        Returns:
            Combined score in [0, 1].
        """
        heat_weight = 0.2
        relevance_weight = 0.4
        views_weight = 0.4

        # Normalize heat to [0, 1] using log scale
        # Typical heat scores range from ~100K to ~5M
        if heat_score > 0:
            norm_heat = min(1.0, math.log1p(heat_score) / math.log1p(5_000_000))
        else:
            norm_heat = 0.0

        # Relevance is already [0, 1]
        norm_relevance = relevance

        # Normalize predicted views using log scale
        if predicted_views is not None and predicted_views > 0:
            # Typical Bilibili views: 1K-1M
            norm_views = min(1.0, math.log1p(predicted_views) / math.log1p(1_000_000))
        else:
            # No prediction available — use neutral value
            norm_views = 0.5

        return (
            heat_weight * norm_heat
            + relevance_weight * norm_relevance
            + views_weight * norm_views
        )
