"""
Model wrapper for video scoring inference.

Loads a trained LightGBM model and provides prediction methods
for single videos and batch feature arrays.
"""
import json
import os
from typing import Dict, List, Optional

import lightgbm as lgb
import numpy as np

from ..db.database import CompetitorVideo
from ..training.features import FEATURE_NAMES, LABEL_NAMES, extract_features_single


class RankerModel:
    """Wrapper around a trained LightGBM booster for video scoring."""

    def __init__(self, model_path: str, metadata_path: Optional[str] = None):
        """Load model from file.

        Args:
            model_path: Path to LightGBM .txt model file.
            metadata_path: Optional path to metadata .json file.

        Raises:
            FileNotFoundError: If model file doesn't exist.
        """
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Model file not found: {model_path}")

        self.model = lgb.Booster(model_file=model_path)
        self.model_path = model_path
        self.metadata: Optional[dict] = None

        if metadata_path and os.path.exists(metadata_path):
            with open(metadata_path, "r") as f:
                self.metadata = json.load(f)

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
        model_path = os.path.join(model_dir, "latest_model.txt")
        meta_path = os.path.join(model_dir, "latest_model_meta.json")
        return cls(model_path, meta_path if os.path.exists(meta_path) else None)

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        """Predict class probabilities.

        Args:
            features: Feature array of shape (n_samples, n_features) or (n_features,).

        Returns:
            Probability array of shape (n_samples, n_classes).
        """
        if features.ndim == 1:
            features = features.reshape(1, -1)
        return self.model.predict(features)

    def predict_label(self, features: np.ndarray) -> List[str]:
        """Predict string labels.

        Args:
            features: Feature array of shape (n_samples, n_features) or (n_features,).

        Returns:
            List of label strings.
        """
        proba = self.predict_proba(features)
        indices = np.argmax(proba, axis=1)
        return [LABEL_NAMES[int(i)] for i in indices]

    def predict_video(self, video: CompetitorVideo) -> Dict:
        """Predict scoring for a single CompetitorVideo.

        Args:
            video: CompetitorVideo instance.

        Returns:
            Dict with 'label', 'probabilities', and 'confidence'.
        """
        feat_dict = extract_features_single(video)
        feat_array = np.array([feat_dict[f] for f in FEATURE_NAMES]).reshape(1, -1)

        proba = self.model.predict(feat_array)[0]
        pred_idx = int(np.argmax(proba))

        return {
            "label": LABEL_NAMES[pred_idx],
            "confidence": float(proba[pred_idx]),
            "probabilities": {
                LABEL_NAMES[i]: float(p) for i, p in enumerate(proba)
            },
        }
