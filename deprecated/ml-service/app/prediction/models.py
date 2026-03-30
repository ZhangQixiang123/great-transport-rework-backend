"""Pydantic models for prediction results."""
import math
from typing import Optional

from pydantic import BaseModel


class VideoPredictionResult(BaseModel):
    """Final prediction result from the neural predictor."""
    label: str  # failed/standard/successful/viral
    predicted_log_views: float
    predicted_views: float
    source: str  # "neural_predictor", "llm", or "lightgbm"

    @staticmethod
    def label_from_log_views(
        log_views: float,
        p25: float = 7.6,
        p75: float = 10.3,
        p95: float = 12.2,
    ) -> str:
        """Classify log_views into a label using percentile thresholds."""
        if log_views < p25:
            return "failed"
        elif log_views < p75:
            return "standard"
        elif log_views < p95:
            return "successful"
        else:
            return "viral"
