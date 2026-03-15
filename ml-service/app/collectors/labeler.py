"""
Auto-Labeler for Competitor Videos

Labels competitor videos based on performance thresholds.
Uses the shared thresholds and functions from bilibili_tracker.
"""
import logging

from ..db.database import Database, CompetitorVideo
from .bilibili_tracker import (
    LABEL_THRESHOLDS,
    calculate_engagement_rate,
    determine_label,
)

logger = logging.getLogger(__name__)


def determine_label_for_video(video: CompetitorVideo) -> str:
    """Determine the success label for a competitor video."""
    engagement = calculate_engagement_rate(
        video.views, video.likes, video.coins, video.favorites
    )
    return determine_label(video.views, engagement, video.coins)


def calculate_video_engagement_rate(video: CompetitorVideo) -> float:
    """Calculate engagement rate for a competitor video."""
    return calculate_engagement_rate(
        video.views, video.likes, video.coins, video.favorites
    )


class Labeler:
    """Auto-labels competitor videos based on performance metrics."""

    def __init__(self, db: Database):
        """
        Initialize the labeler.

        Args:
            db: Database instance for reading/writing labels
        """
        self.db = db

    def label_video(self, video: CompetitorVideo) -> str:
        """
        Label a single video and update the database.

        Args:
            video: CompetitorVideo to label

        Returns:
            The assigned label
        """
        label = determine_label_for_video(video)

        # Update in database
        self.db.update_competitor_video_label(video.bvid, label)

        engagement = calculate_video_engagement_rate(video)
        logger.info(
            f"Labeled {video.bvid} as '{label}' "
            f"(views={video.views:,}, engagement={engagement:.2%}, coins={video.coins:,})"
        )

        return label

    def label_all_unlabeled(self, limit: int = 1000) -> dict:
        """
        Label all unlabeled competitor videos.

        Args:
            limit: Maximum number of videos to label

        Returns:
            Dict with labeling statistics
        """
        videos = self.db.get_unlabeled_competitor_videos(limit=limit)
        logger.info(f"Found {len(videos)} unlabeled videos to label")

        results = {
            "total": len(videos),
            "viral": 0,
            "successful": 0,
            "standard": 0,
            "failed": 0,
            "errors": 0
        }

        for video in videos:
            try:
                label = self.label_video(video)
                results[label] += 1
            except Exception as e:
                logger.error(f"Error labeling video {video.bvid}: {e}")
                results["errors"] += 1

        logger.info(
            f"Labeled {results['total'] - results['errors']} videos: "
            f"viral={results['viral']}, successful={results['successful']}, "
            f"standard={results['standard']}, failed={results['failed']}"
        )

        return results

    def relabel_all(self, limit: int = 10000) -> dict:
        """
        Relabel all competitor videos (including already labeled ones).

        Useful when thresholds are updated.

        Args:
            limit: Maximum number of videos to relabel

        Returns:
            Dict with labeling statistics
        """
        videos = self.db.get_competitor_videos(limit=limit)
        logger.info(f"Relabeling {len(videos)} videos")

        results = {
            "total": len(videos),
            "viral": 0,
            "successful": 0,
            "standard": 0,
            "failed": 0,
            "unchanged": 0,
            "errors": 0
        }

        for video in videos:
            try:
                old_label = video.label
                new_label = determine_label_for_video(video)

                if old_label == new_label:
                    results["unchanged"] += 1
                else:
                    self.db.update_competitor_video_label(video.bvid, new_label)
                    results[new_label] += 1
                    logger.debug(f"Relabeled {video.bvid}: {old_label} -> {new_label}")

            except Exception as e:
                logger.error(f"Error relabeling video {video.bvid}: {e}")
                results["errors"] += 1

        return results

    def get_label_distribution(self) -> dict:
        """
        Get the current distribution of labels.

        Returns:
            Dict with label counts
        """
        return self.db.get_training_data_summary()
