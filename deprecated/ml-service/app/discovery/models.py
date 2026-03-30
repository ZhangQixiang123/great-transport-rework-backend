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


class TranslatedKeyword(BaseModel):
    """LLM-translated search queries from a Chinese keyword."""
    english_queries: list[str]  # 2-3 English YouTube search queries
    topic_summary: str  # Brief English summary of the trending topic


class TranslatedTitle(BaseModel):
    """LLM-translated video title for Bilibili upload."""
    chinese_title: str


class RelevanceResult(BaseModel):
    """LLM-scored relevance between a keyword and a YouTube video."""
    relevance_score: float  # 0.0-1.0
    reasoning: str
    detected_topics: list[str]
    is_relevant: bool  # True if score >= 0.5


class CandidateEvaluation(BaseModel):
    """LLM final evaluation of a candidate video for transport."""
    predicted_log_views: float  # log1p(predicted bilibili views)
    predicted_views: float  # expm1(predicted_log_views)
    confidence: float  # 0.0-1.0
    label: str  # failed/standard/successful/viral
    reasoning: str


@dataclass
class Recommendation:
    """A ranked recommendation from the discovery pipeline."""
    # Source info
    strategy: str  # which transport strategy found this
    query_used: str  # the YouTube search query that found this

    # YouTube video info
    youtube_video_id: str
    youtube_title: str
    youtube_channel: str
    youtube_views: int
    youtube_likes: int
    youtube_duration_seconds: int

    # Evaluation results
    nn_prediction: Optional[float]  # neural predictor log_views
    novelty_score: float  # 0.0-1.0 (1.0 = very novel on Bilibili)
    predicted_log_views: Optional[float]  # LLM final prediction
    predicted_views: Optional[float]
    predicted_label: Optional[str]  # failed/standard/successful/viral
    confidence: float
    reasoning: str

    # Final ranking
    combined_score: float

    # Legacy compat (used by DB save)
    keyword: str = ""
    heat_score: int = 0
    relevance_score: float = 0.0
    relevance_reasoning: str = ""
