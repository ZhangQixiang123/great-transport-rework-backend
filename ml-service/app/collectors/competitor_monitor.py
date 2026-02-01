"""
Competitor Channel Monitor

Collects video data from Bilibili transporter channels for ML training data.
Uses bilibili-api-python to fetch channel info and video metrics.
"""
import asyncio
import logging
import re
import time
from datetime import datetime
from typing import Optional, List, Tuple

from bilibili_api import user, video, exceptions

from ..db.database import Database, CompetitorChannel, CompetitorVideo
from .bilibili_tracker import RateLimiter

logger = logging.getLogger(__name__)

# Patterns for extracting YouTube video IDs from titles/descriptions
YOUTUBE_ID_PATTERNS = [
    # [VIDEO_ID] format
    r'\[([a-zA-Z0-9_-]{11})\]',
    # (source: VIDEO_ID) format
    r'\(source:\s*([a-zA-Z0-9_-]{11})\)',
    # youtube.com/watch?v=VIDEO_ID format
    r'youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
    # youtu.be/VIDEO_ID format
    r'youtu\.be/([a-zA-Z0-9_-]{11})',
    # yt: VIDEO_ID format
    r'yt:\s*([a-zA-Z0-9_-]{11})',
    # YouTube: VIDEO_ID format
    r'YouTube:\s*([a-zA-Z0-9_-]{11})',
    # source=VIDEO_ID format
    r'source=([a-zA-Z0-9_-]{11})',
    # Original: VIDEO_ID format
    r'Original:\s*([a-zA-Z0-9_-]{11})',
]


def extract_youtube_source_id(title: str, description: str) -> Optional[str]:
    """
    Extract potential YouTube video ID from title or description.

    Args:
        title: Video title
        description: Video description

    Returns:
        YouTube video ID if found, None otherwise
    """
    # Combine title and description for searching
    text = f"{title} {description}"

    for pattern in YOUTUBE_ID_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            video_id = match.group(1)
            # Validate it looks like a YouTube ID (11 chars, alphanumeric with - and _)
            if len(video_id) == 11 and re.match(r'^[a-zA-Z0-9_-]+$', video_id):
                return video_id

    return None


class CompetitorMonitor:
    """Monitors competitor channels on Bilibili and collects video data."""

    def __init__(self, db: Database, rate_limit: float = 1.0):
        """
        Initialize the monitor.

        Args:
            db: Database instance for storing data.
            rate_limit: Minimum seconds between API requests (default 1.0)
        """
        self.db = db
        self.rate_limiter = RateLimiter(min_interval=rate_limit, max_retries=3)

    async def get_channel_info(self, uid: str) -> Optional[CompetitorChannel]:
        """
        Fetch channel information from Bilibili.

        Args:
            uid: Bilibili user ID (mid)

        Returns:
            CompetitorChannel or None if not found
        """
        try:
            u = user.User(uid=int(uid))
            info = await self.rate_limiter.execute_with_retry(u.get_user_info())

            if info is None:
                return None

            return CompetitorChannel(
                bilibili_uid=uid,
                name=info.get("name", ""),
                description=info.get("sign", ""),
                follower_count=info.get("fans", 0),
                video_count=0,  # Will be updated when fetching videos
                added_at=datetime.utcnow(),
                is_active=True
            )
        except exceptions.ResponseCodeException as e:
            if e.code in (-404, -400):  # User not found
                logger.warning(f"User {uid} not found")
                return None
            raise
        except Exception as e:
            logger.error(f"Error getting channel info for {uid}: {e}")
            raise

    async def get_video_stats(self, bvid: str) -> Optional[dict]:
        """
        Fetch detailed video statistics.

        Args:
            bvid: Bilibili video ID

        Returns:
            Dict with video stats or None if not found
        """
        try:
            v = video.Video(bvid=bvid)
            info = await self.rate_limiter.execute_with_retry(v.get_info())

            if info is None:
                return None

            stat = info.get("stat", {})
            return {
                "bvid": bvid,
                "title": info.get("title", ""),
                "description": info.get("desc", ""),
                "duration": info.get("duration", 0),
                "views": stat.get("view", 0),
                "likes": stat.get("like", 0),
                "coins": stat.get("coin", 0),
                "favorites": stat.get("favorite", 0),
                "shares": stat.get("share", 0),
                "danmaku": stat.get("danmaku", 0),
                "comments": stat.get("reply", 0),
                "publish_time": datetime.fromtimestamp(info.get("pubdate", 0)) if info.get("pubdate") else None,
            }
        except exceptions.ResponseCodeException as e:
            if e.code in (-404, 62002):  # Video not found or deleted
                logger.warning(f"Video {bvid} not found or deleted")
                return None
            raise
        except Exception as e:
            logger.error(f"Error getting video stats for {bvid}: {e}")
            raise

    async def get_recent_videos(self, uid: str, count: int = 100) -> List[dict]:
        """
        Fetch recent videos from a channel.

        Args:
            uid: Bilibili user ID (mid)
            count: Number of videos to fetch (max ~100 per page)

        Returns:
            List of video info dicts
        """
        videos = []
        page = 1
        page_size = min(count, 50)  # API typically returns up to 50 per page

        try:
            u = user.User(uid=int(uid))

            while len(videos) < count:
                await self.rate_limiter.wait()

                # Get videos page
                result = await u.get_videos(pn=page, ps=page_size)

                if result is None:
                    break

                video_list = result.get("list", {}).get("vlist", [])
                if not video_list:
                    break

                for v in video_list:
                    videos.append({
                        "bvid": v.get("bvid", ""),
                        "title": v.get("title", ""),
                        "description": v.get("description", ""),
                        "duration": self._parse_duration(v.get("length", "0:00")),
                        "views": v.get("play", 0),
                        "publish_time": datetime.fromtimestamp(v.get("created", 0)) if v.get("created") else None,
                    })

                    if len(videos) >= count:
                        break

                page += 1

                # Check if we've reached the end
                if len(video_list) < page_size:
                    break

        except exceptions.ResponseCodeException as e:
            if e.code in (-404, -400):
                logger.warning(f"User {uid} not found or has no videos")
                return []
            raise
        except Exception as e:
            logger.error(f"Error getting videos for {uid}: {e}")
            raise

        return videos

    def _parse_duration(self, duration_str: str) -> int:
        """Parse duration string (MM:SS or HH:MM:SS) to seconds."""
        try:
            parts = duration_str.split(":")
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            elif len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            return 0
        except (ValueError, IndexError):
            return 0

    async def collect_channel(self, uid: str, video_count: int = 100) -> Tuple[int, int]:
        """
        Collect videos from a competitor channel.

        Args:
            uid: Bilibili user ID (mid)
            video_count: Number of videos to collect

        Returns:
            Tuple of (videos_collected, videos_with_youtube_source)
        """
        logger.info(f"Collecting videos from channel {uid}")

        # Get channel info
        channel = await self.get_channel_info(uid)
        if channel is None:
            logger.warning(f"Could not get channel info for {uid}")
            return 0, 0

        # Get recent videos
        videos = await self.get_recent_videos(uid, video_count)
        logger.info(f"Found {len(videos)} videos for channel {uid}")

        # Update channel video count
        channel.video_count = len(videos)
        self.db.add_competitor_channel(channel)

        collected = 0
        with_youtube = 0

        for v in videos:
            try:
                # Get detailed stats for each video
                stats = await self.get_video_stats(v["bvid"])
                if stats is None:
                    continue

                # Try to extract YouTube source ID
                youtube_id = extract_youtube_source_id(
                    stats.get("title", ""),
                    stats.get("description", "")
                )

                # Create competitor video record
                comp_video = CompetitorVideo(
                    bvid=stats["bvid"],
                    bilibili_uid=uid,
                    title=stats["title"],
                    description=stats["description"],
                    duration=stats["duration"],
                    views=stats["views"],
                    likes=stats["likes"],
                    coins=stats["coins"],
                    favorites=stats["favorites"],
                    shares=stats["shares"],
                    danmaku=stats["danmaku"],
                    comments=stats["comments"],
                    publish_time=stats["publish_time"],
                    collected_at=datetime.utcnow(),
                    youtube_source_id=youtube_id,
                    label=None  # Will be set by labeler
                )

                self.db.save_competitor_video(comp_video)
                collected += 1

                if youtube_id:
                    with_youtube += 1
                    logger.debug(f"Found YouTube source {youtube_id} for {stats['bvid']}")

            except Exception as e:
                logger.error(f"Error collecting video {v['bvid']}: {e}")
                continue

        logger.info(f"Collected {collected} videos from {uid}, {with_youtube} with YouTube source")
        return collected, with_youtube

    async def collect_all_active(self, video_count_per_channel: int = 100) -> dict:
        """
        Collect videos from all active competitor channels.

        Args:
            video_count_per_channel: Number of videos per channel

        Returns:
            Dict with collection statistics
        """
        channels = self.db.list_competitor_channels(active_only=True)
        logger.info(f"Collecting from {len(channels)} active competitor channels")

        results = {
            "channels_processed": 0,
            "total_videos": 0,
            "with_youtube_source": 0,
            "errors": 0
        }

        for channel in channels:
            try:
                collected, with_youtube = await self.collect_channel(
                    channel.bilibili_uid,
                    video_count_per_channel
                )
                results["channels_processed"] += 1
                results["total_videos"] += collected
                results["with_youtube_source"] += with_youtube
            except Exception as e:
                logger.error(f"Error collecting from channel {channel.bilibili_uid}: {e}")
                results["errors"] += 1

        return results
