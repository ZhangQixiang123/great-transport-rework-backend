"""
Bilibili Performance Tracker

Collects video performance metrics from Bilibili using bilibili-api-python.
Tracks metrics at scheduled checkpoints and auto-labels based on thresholds.
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

from bilibili_api import video, exceptions

from ..db.database import Database, Upload, UploadPerformance, UploadOutcome

logger = logging.getLogger(__name__)

# Checkpoint hours for tracking
CHECKPOINTS = [1, 6, 24, 48, 168, 720]  # 1h, 6h, 24h, 48h, 7d, 30d

# Success label thresholds
LABEL_THRESHOLDS = {
    "viral": {
        "min_views": 1_000_000,
        "min_engagement_rate": 0.05,  # 5%
        "min_coins": 10_000,
    },
    "successful": {
        "min_views": 100_000,
        "min_engagement_rate": 0.03,  # 3%
    },
    "standard": {
        "min_views": 10_000,
        "min_engagement_rate": 0.01,  # 1%
        "max_engagement_rate": 0.03,  # 3%
    },
    "failed": {
        "max_views": 10_000,
        # or engagement_rate < 1%
    },
}


@dataclass
class BilibiliVideoStats:
    """Video statistics from Bilibili."""
    bvid: str
    views: int
    likes: int
    coins: int
    favorites: int
    shares: int
    danmaku: int
    comments: int


class RateLimiter:
    """Simple rate limiter with exponential backoff."""

    def __init__(self, min_interval: float = 1.0, max_retries: int = 3):
        self.min_interval = min_interval
        self.max_retries = max_retries
        self._last_request = 0.0

    async def wait(self) -> None:
        """Wait for the minimum interval since last request."""
        now = time.time()
        elapsed = now - self._last_request
        if elapsed < self.min_interval:
            await asyncio.sleep(self.min_interval - elapsed)
        self._last_request = time.time()

    async def execute_with_retry(self, coro):
        """Execute a coroutine with retry and exponential backoff."""
        last_error = None
        for attempt in range(self.max_retries):
            try:
                await self.wait()
                return await coro
            except exceptions.ResponseCodeException as e:
                last_error = e
                if e.code == -412:  # Rate limited
                    wait_time = (2 ** attempt) * 2  # 2, 4, 8 seconds
                    logger.warning(f"Rate limited, waiting {wait_time}s (attempt {attempt + 1}/{self.max_retries})")
                    await asyncio.sleep(wait_time)
                elif e.code in (-404, 62002):  # Video not found or deleted
                    logger.warning(f"Video not found: {e}")
                    return None
                else:
                    raise
            except Exception as e:
                last_error = e
                wait_time = (2 ** attempt)
                logger.warning(f"Error: {e}, retrying in {wait_time}s (attempt {attempt + 1}/{self.max_retries})")
                await asyncio.sleep(wait_time)

        logger.error(f"Max retries exceeded: {last_error}")
        raise last_error


class BilibiliTracker:
    """Tracks Bilibili video performance metrics."""

    def __init__(self, db: Database):
        """
        Initialize the tracker.

        Args:
            db: Database instance for storing metrics.
        """
        self.db = db
        self.rate_limiter = RateLimiter(min_interval=1.0, max_retries=3)

    async def get_video_stats(self, bvid: str) -> Optional[BilibiliVideoStats]:
        """
        Get current statistics for a Bilibili video.

        Args:
            bvid: Bilibili video ID (e.g., BV1xx411x7xx)

        Returns:
            Video statistics or None if video not found.
        """
        try:
            v = video.Video(bvid=bvid)
            info = await self.rate_limiter.execute_with_retry(v.get_info())

            if info is None:
                return None

            stat = info.get("stat", {})
            return BilibiliVideoStats(
                bvid=bvid,
                views=stat.get("view", 0),
                likes=stat.get("like", 0),
                coins=stat.get("coin", 0),
                favorites=stat.get("favorite", 0),
                shares=stat.get("share", 0),
                danmaku=stat.get("danmaku", 0),
                comments=stat.get("reply", 0),
            )
        except exceptions.ResponseCodeException as e:
            if e.code in (-404, 62002):  # Video not found or deleted
                logger.warning(f"Video {bvid} not found or deleted")
                return None
            raise
        except Exception as e:
            logger.error(f"Error getting stats for {bvid}: {e}")
            raise

    def calculate_metrics(
        self,
        stats: BilibiliVideoStats,
        upload: Upload,
        checkpoint_hours: int
    ) -> UploadPerformance:
        """
        Calculate derived metrics from raw statistics.

        Args:
            stats: Raw video statistics.
            upload: Upload record.
            checkpoint_hours: Current checkpoint.

        Returns:
            UploadPerformance with calculated metrics.
        """
        # Calculate view velocity (views per hour since upload)
        hours_since_upload = checkpoint_hours
        view_velocity = stats.views / max(hours_since_upload, 1)

        # Calculate engagement rate: (likes + coins + favorites) / views
        total_engagement = stats.likes + stats.coins + stats.favorites
        engagement_rate = total_engagement / max(stats.views, 1)

        return UploadPerformance(
            id=None,
            upload_id=upload.video_id,
            checkpoint_hours=checkpoint_hours,
            recorded_at=datetime.utcnow(),
            views=stats.views,
            likes=stats.likes,
            coins=stats.coins,
            favorites=stats.favorites,
            shares=stats.shares,
            danmaku=stats.danmaku,
            comments=stats.comments,
            view_velocity=view_velocity,
            engagement_rate=engagement_rate,
        )

    async def collect_metrics(self, upload: Upload, checkpoint_hours: int) -> Optional[UploadPerformance]:
        """
        Collect and store metrics for an upload at a checkpoint.

        Args:
            upload: Upload to collect metrics for.
            checkpoint_hours: Checkpoint to record.

        Returns:
            UploadPerformance record or None if video not found.
        """
        logger.info(f"Collecting metrics for {upload.bilibili_bvid} at {checkpoint_hours}h checkpoint")

        stats = await self.get_video_stats(upload.bilibili_bvid)
        if stats is None:
            logger.warning(f"Could not get stats for {upload.bilibili_bvid}")
            return None

        perf = self.calculate_metrics(stats, upload, checkpoint_hours)
        self.db.save_performance(perf)

        logger.info(
            f"Saved metrics for {upload.bilibili_bvid}: "
            f"views={perf.views}, engagement={perf.engagement_rate:.2%}"
        )
        return perf

    def determine_label(self, perf: UploadPerformance) -> str:
        """
        Determine the success label based on performance metrics.

        Args:
            perf: Performance metrics.

        Returns:
            Label: 'viral', 'successful', 'standard', or 'failed'
        """
        views = perf.views
        engagement = perf.engagement_rate
        coins = perf.coins

        # Check for viral
        viral = LABEL_THRESHOLDS["viral"]
        if (views >= viral["min_views"] and
            engagement >= viral["min_engagement_rate"] and
            coins >= viral["min_coins"]):
            return "viral"

        # Check for successful
        successful = LABEL_THRESHOLDS["successful"]
        if views >= successful["min_views"] and engagement >= successful["min_engagement_rate"]:
            return "successful"

        # Check for standard
        standard = LABEL_THRESHOLDS["standard"]
        if (views >= standard["min_views"] and
            standard["min_engagement_rate"] <= engagement <= standard["max_engagement_rate"]):
            return "standard"

        # Default to failed
        return "failed"

    async def auto_label(self, upload: Upload) -> Optional[UploadOutcome]:
        """
        Auto-label an upload based on its latest performance metrics.

        Args:
            upload: Upload to label.

        Returns:
            UploadOutcome or None if no performance data available.
        """
        perf = self.db.get_latest_performance(upload.video_id)
        if perf is None:
            logger.warning(f"No performance data for {upload.video_id}")
            return None

        label = self.determine_label(perf)

        outcome = UploadOutcome(
            id=None,
            upload_id=upload.video_id,
            label=label,
            labeled_at=datetime.utcnow(),
            final_views=perf.views,
            final_engagement_rate=perf.engagement_rate,
            final_coins=perf.coins,
        )

        self.db.save_outcome(outcome)
        logger.info(f"Labeled {upload.video_id} as '{label}' (views={perf.views}, engagement={perf.engagement_rate:.2%})")

        return outcome

    async def track_all_due(self) -> Dict[str, int]:
        """
        Track all uploads that are due for any checkpoint.

        Returns:
            Dictionary with counts of tracked uploads per checkpoint.
        """
        results = {}
        for checkpoint in CHECKPOINTS:
            uploads = self.db.get_uploads_for_tracking(checkpoint)
            count = 0
            for upload in uploads:
                try:
                    perf = await self.collect_metrics(upload, checkpoint)
                    if perf:
                        count += 1
                except Exception as e:
                    logger.error(f"Error tracking {upload.bilibili_bvid}: {e}")
            results[f"{checkpoint}h"] = count
            if count > 0:
                logger.info(f"Tracked {count} uploads at {checkpoint}h checkpoint")

        return results

    async def label_all_due(self, min_checkpoint: int = 168) -> int:
        """
        Auto-label all uploads that have sufficient data.

        Args:
            min_checkpoint: Minimum checkpoint hours required (default 168 = 7 days)

        Returns:
            Number of uploads labeled.
        """
        uploads = self.db.get_uploads_for_labeling(min_checkpoint)
        count = 0
        for upload in uploads:
            try:
                outcome = await self.auto_label(upload)
                if outcome:
                    count += 1
            except Exception as e:
                logger.error(f"Error labeling {upload.video_id}: {e}")

        logger.info(f"Auto-labeled {count} uploads")
        return count
