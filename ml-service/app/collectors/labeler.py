"""
Auto-Labeler for Competitor Videos

Labels competitor videos based on performance thresholds.
Uses the same thresholds as BilibiliTracker for consistency.
"""
import logging
from typing import Optional

from ..db.database import Database, CompetitorVideo

logger = logging.getLogger(__name__)

# Success label thresholds (matching BilibiliTracker)
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


def calculate_engagement_rate(video: CompetitorVideo) -> float:
    """
    Calculate engagement rate for a competitor video.

    Formula: (likes + coins + favorites) / views

    Args:
        video: CompetitorVideo to analyze

    Returns:
        Engagement rate as a decimal (e.g., 0.05 for 5%)
    """
    if video.views <= 0:
        return 0.0

    total_engagement = video.likes + video.coins + video.favorites
    return total_engagement / video.views


def determine_label(video: CompetitorVideo) -> str:
    """
    Determine the success label based on performance metrics.

    Args:
        video: CompetitorVideo to label

    Returns:
        Label: 'viral', 'successful', 'standard', or 'failed'
    """
    views = video.views
    coins = video.coins
    engagement = calculate_engagement_rate(video)

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
        label = determine_label(video)

        # Update in database
        self.db.update_competitor_video_label(video.bvid, label)

        engagement = calculate_engagement_rate(video)
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
                new_label = determine_label(video)

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
