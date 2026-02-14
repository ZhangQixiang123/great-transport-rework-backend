"""
Data models for the discovery pipeline.
"""
from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel


@dataclass
class TrendingKeyword:
    """A trending keyword from Bilibili hot search."""
    keyword: str
    heat_score: int
    position: int
    is_commercial: bool


@dataclass
class YouTubeCandidate:
    """A YouTube video found via keyword search."""
    video_id: str
    title: str
    channel_title: str
    description: str
    views: int
    likes: int
    comments: int
    duration_seconds: int
    category_id: int
    tags: list[str]
    published_at: str
    thumbnail_url: str


class RelevanceResult(BaseModel):
    """LLM-scored relevance between a keyword and a YouTube video."""
    relevance_score: float  # 0.0-1.0
    reasoning: str
    detected_topics: list[str]
    is_relevant: bool  # True if score >= 0.5


@dataclass
class Recommendation:
    """A ranked recommendation from the discovery pipeline."""
    keyword: str
    heat_score: int
    youtube_video_id: str
    youtube_title: str
    youtube_channel: str
    youtube_views: int
    youtube_likes: int
    youtube_duration_seconds: int
    relevance_score: float
    relevance_reasoning: str
    predicted_log_views: Optional[float]
    predicted_views: Optional[float]
    predicted_label: Optional[str]  # failed/standard/successful/viral
    combined_score: float
