"""Data-calibrated heuristic scoring for video candidates."""
import json
import math
import logging
from dataclasses import dataclass, field
from typing import Dict, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ScoringParams:
    """Scoring parameters — each persona can tune these independently."""
    engagement_good_threshold: float = 0.04
    engagement_weight: float = 0.3
    view_signal_weight: float = 0.2
    opportunity_weight: float = 0.3
    duration_weight: float = 0.2
    duration_sweet_spot: Tuple[int, int] = (300, 900)
    category_bonuses: Dict[int, float] = field(default_factory=dict)
    youtube_min_views: int = 10_000
    bilibili_success_threshold: int = 50_000

    def to_json(self) -> str:
        return json.dumps({
            "engagement_good_threshold": self.engagement_good_threshold,
            "engagement_weight": self.engagement_weight,
            "view_signal_weight": self.view_signal_weight,
            "opportunity_weight": self.opportunity_weight,
            "duration_weight": self.duration_weight,
            "duration_sweet_spot": list(self.duration_sweet_spot),
            "category_bonuses": {str(k): v for k, v in self.category_bonuses.items()},
            "youtube_min_views": self.youtube_min_views,
            "bilibili_success_threshold": self.bilibili_success_threshold,
        })

    @classmethod
    def from_json(cls, json_str: str) -> "ScoringParams":
        data = json.loads(json_str)
        return cls(
            engagement_good_threshold=data.get("engagement_good_threshold", 0.04),
            engagement_weight=data.get("engagement_weight", 0.3),
            view_signal_weight=data.get("view_signal_weight", 0.2),
            opportunity_weight=data.get("opportunity_weight", 0.3),
            duration_weight=data.get("duration_weight", 0.2),
            duration_sweet_spot=tuple(data.get("duration_sweet_spot", [300, 900])),
            category_bonuses={
                int(k): v for k, v in data.get("category_bonuses", {}).items()
            },
            youtube_min_views=data.get("youtube_min_views", 10_000),
            bilibili_success_threshold=data.get("bilibili_success_threshold", 50_000),
        )


def heuristic_score(
    candidate_views: int,
    likes: int,
    duration: int,
    category_id: int,
    opportunity_score: float,
    params: ScoringParams,
) -> float:
    """Score a YouTube video candidate using data-calibrated heuristic."""
    like_ratio = likes / max(candidate_views, 1)
    engagement = min(1.0, like_ratio / params.engagement_good_threshold)

    view_signal = min(1.0, math.log1p(candidate_views) / math.log1p(1_000_000))

    lo, hi = params.duration_sweet_spot
    duration_score = 1.0 if lo <= duration <= hi else 0.7

    category_bonus = params.category_bonuses.get(category_id, 1.0)

    raw = (
        engagement * params.engagement_weight
        + view_signal * params.view_signal_weight
        + opportunity_score * params.opportunity_weight
        + duration_score * params.duration_weight
    )

    return raw * category_bonus
