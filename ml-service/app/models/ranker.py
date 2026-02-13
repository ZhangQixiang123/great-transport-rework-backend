"""
Model wrapper for video scoring inference.

Loads a trained model (GPBoost or LightGBM) and provides prediction methods
for single videos and batch feature arrays.

GPBoost mode (use_random_intercepts=True):
  For known channels: uses fixed effects (tree) + random intercept (channel baseline).
  For unknown channels: uses fixed effects only (random effect = 0).

LightGBM mode (use_random_intercepts=False):
  Pure fixed effects — same prediction quality for known and unknown channels.
"""
import json
import math
import os
from typing import Dict, List, Optional

import gpboost as gpb
import lightgbm as lgb
import numpy as np

from ..db.database import CompetitorVideo
from ..training.features import FEATURE_NAMES, LABEL_NAMES, extract_features_single


class RankerModel:
    """Wrapper around a trained GPBoost/LightGBM booster for video scoring."""

    def __init__(self, model_path: str, metadata_path: Optional[str] = None):
        """Load model from file.

        Args:
            model_path: Path to model .json file.
            metadata_path: Optional path to metadata .json file.

        Raises:
            FileNotFoundError: If model file doesn't exist.
        """
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Model file not found: {model_path}")

        self.model_path = model_path
        self.metadata: Optional[dict] = None

        # Loaded from metadata
        self._yt_imputation_stats: Dict = {}
        self._percentile_thresholds: Dict[str, float] = {}
        self._feature_names: List[str] = list(FEATURE_NAMES)
        self._use_random_intercepts: bool = True

        if metadata_path and os.path.exists(metadata_path):
            with open(metadata_path, "r", encoding="utf-8") as f:
                self.metadata = json.load(f)
            self._load_metadata()

        # Load model with appropriate library
        if self._use_random_intercepts:
            self.model = gpb.Booster(model_file=model_path)
        else:
            self.model = lgb.Booster(model_file=model_path)

    def _load_metadata(self):
        """Extract inference-relevant data from metadata."""
        if not self.metadata:
            return
        self._yt_imputation_stats = self.metadata.get("yt_imputation_stats", {})
        self._percentile_thresholds = self.metadata.get("percentile_thresholds", {})
        self._use_random_intercepts = self.metadata.get("use_random_intercepts", True)
        saved_names = self.metadata.get("feature_names")
        if saved_names:
            self._feature_names = saved_names

    @classmethod
    def load_latest(cls, model_dir: str = "models") -> "RankerModel":
        """Load the latest trained model.

        Args:
            model_dir: Directory containing model artifacts.

        Returns:
            RankerModel instance.

        Raises:
            FileNotFoundError: If no latest model exists.
        """
        model_path = os.path.join(model_dir, "latest_model.json")
        meta_path = os.path.join(model_dir, "latest_model_meta.json")
        return cls(model_path, meta_path if os.path.exists(meta_path) else None)

    def predict_video(
        self,
        video: CompetitorVideo,
        yt_stats: Optional[Dict] = None,
        title_embedding: Optional[np.ndarray] = None,
    ) -> Dict:
        """Predict scoring for a single CompetitorVideo.

        Args:
            video: CompetitorVideo instance.
            yt_stats: Optional YouTube stats dict. If None and imputation stats
                      are available, stats will be imputed.
            title_embedding: Optional PCA-reduced title embedding.

        Returns:
            Dict with 'label', 'predicted_log_views', 'predicted_views'.
        """
        # YouTube stats: use provided, impute, or None
        yt_imputed = False
        if yt_stats is None and self._yt_imputation_stats:
            ch_imp = self._yt_imputation_stats.get("per_channel", {}).get(video.bilibili_uid)
            if ch_imp:
                yt_stats = dict(ch_imp)
            else:
                global_imp = self._yt_imputation_stats.get("global", {})
                if global_imp:
                    yt_stats = dict(global_imp)
            if yt_stats:
                yt_imputed = True

        # Extract features
        feat_dict = extract_features_single(
            video, yt_stats=yt_stats, yt_imputed=yt_imputed,
            title_embedding=title_embedding,
        )
        feat_array = np.array([feat_dict[f] for f in self._feature_names]).reshape(1, -1)

        if self._use_random_intercepts:
            group_data = np.array([video.bilibili_uid])
            pred_dict = self.model.predict(data=feat_array, group_data_pred=group_data)
            predicted_log_views = float(np.array(pred_dict["response_mean"])[0])
        else:
            predicted_log_views = float(self.model.predict(feat_array)[0])

        label = self._classify(predicted_log_views)

        return {
            "label": label,
            "predicted_log_views": predicted_log_views,
            "predicted_views": math.expm1(predicted_log_views),
        }

    def _classify(self, log_views: float) -> str:
        """Classify a predicted log_views into a label category."""
        p25 = self._percentile_thresholds.get("p25", 0.0)
        p75 = self._percentile_thresholds.get("p75", 0.0)
        p95 = self._percentile_thresholds.get("p95", 0.0)

        if log_views < p25:
            return "failed"
        elif log_views < p75:
            return "standard"
        elif log_views < p95:
            return "successful"
        else:
            return "viral"

    def predict_raw(self, features: np.ndarray, group_data: np.ndarray) -> np.ndarray:
        """Predict with the model.

        Args:
            features: Feature array of shape (n_samples, n_features).
            group_data: Group array of shape (n_samples,) with channel UIDs.
                        Used only in random intercept mode.

        Returns:
            Array of predicted log_views.
        """
        if features.ndim == 1:
            features = features.reshape(1, -1)
        if self._use_random_intercepts:
            pred_dict = self.model.predict(data=features, group_data_pred=group_data)
            return np.array(pred_dict["response_mean"])
        else:
            return self.model.predict(features)
