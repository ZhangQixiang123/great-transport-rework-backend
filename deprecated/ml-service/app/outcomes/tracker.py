"""Outcome tracking — manages the two feedback loops for skill improvement.

Loop 1 (fast): Query yield tracking — did queries find good YouTube videos?
Loop 2 (slow): Bilibili outcome tracking — how did transported videos perform?
"""
import logging
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class OutcomeTracker:
    """Tracks discovery outcomes across both feedback loops."""

    def __init__(self, db):
        """Initialize with a connected Database instance."""
        self.db = db

    # ── Loop 1: Query yield (called after each discovery run) ────────────

    def record_query_yield(
        self,
        run_id: int,
        result_count: int,
        avg_views: int,
        best_video: Optional[dict] = None,
    ) -> None:
        """Record whether a query found good YouTube results.

        Args:
            run_id: Strategy run ID from save_strategy_run().
            result_count: Number of YouTube results returned.
            avg_views: Average view count of results.
            best_video: Dict with best video info (id, title, channel, views, etc.)
        """
        yield_success = 1 if (result_count > 0 and best_video is not None) else 0

        update_kwargs: Dict[str, Any] = {
            "query_result_count": result_count,
            "query_avg_views": avg_views,
            "yield_success": yield_success,
        }

        if best_video:
            update_kwargs.update({
                "youtube_video_id": best_video.get("id"),
                "youtube_title": best_video.get("title"),
                "youtube_channel": best_video.get("channel"),
                "youtube_views": best_video.get("views"),
                "youtube_likes": best_video.get("likes"),
                "youtube_category_id": best_video.get("category_id"),
                "youtube_duration_seconds": best_video.get("duration_seconds"),
            })

        self.db.update_strategy_run(run_id, **update_kwargs)

    def update_strategy_yield_stats(self, strategy_id: int) -> None:
        """Recompute yield stats for a strategy from its runs.

        Args:
            strategy_id: The strategy ID to update.
        """
        if not self.db._conn:
            raise RuntimeError("Database not connected")

        row = self.db._conn.execute("""
            SELECT COUNT(*) as total,
                   SUM(yield_success) as yielded
            FROM strategy_runs
            WHERE strategy_id = ?
        """, (strategy_id,)).fetchone()

        total = row["total"] or 0
        yielded = row["yielded"] or 0

        self.db.update_strategy_stats(
            strategy_id,
            total_queries=total,
            yielded_queries=yielded,
        )

    # ── Loop 2: Bilibili outcomes (called via update-outcomes) ───────────

    def mark_transported(
        self, youtube_video_id: str, bilibili_bvid: str,
    ) -> None:
        """Mark a YouTube video as transported to Bilibili.

        Args:
            youtube_video_id: The YouTube video ID.
            bilibili_bvid: The Bilibili BV ID of the transport.
        """
        if not self.db._conn:
            raise RuntimeError("Database not connected")

        # Find the strategy run for this video
        row = self.db._conn.execute("""
            SELECT id FROM strategy_runs
            WHERE youtube_video_id = ?
            ORDER BY id DESC LIMIT 1
        """, (youtube_video_id,)).fetchone()

        if row:
            self.db.update_strategy_run(
                row["id"],
                was_transported=1,
                bilibili_bvid=bilibili_bvid,
            )

    def update_bilibili_views(self, bvid: str, views: int) -> None:
        """Record Bilibili views for a transported video and compute outcome.

        Args:
            bvid: Bilibili BV ID.
            views: Current Bilibili view count.
        """
        if not self.db._conn:
            raise RuntimeError("Database not connected")

        # Get success threshold from scoring params
        params_row = self.db.get_scoring_params()
        if params_row:
            import json
            params = json.loads(params_row["params_json"])
            threshold = params.get("bilibili_success_threshold", 50_000)
        else:
            threshold = 50_000

        outcome = "success" if views >= threshold else "failure"

        # Find the strategy run for this bvid
        row = self.db._conn.execute("""
            SELECT id FROM strategy_runs
            WHERE bilibili_bvid = ?
            ORDER BY id DESC LIMIT 1
        """, (bvid,)).fetchone()

        if row:
            self.db.update_strategy_run(
                row["id"],
                bilibili_views=views,
                outcome=outcome,
                outcome_recorded_at=datetime.now().isoformat(),
            )
