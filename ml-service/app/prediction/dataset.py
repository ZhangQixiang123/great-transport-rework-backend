"""PyTorch Dataset for the neural predictor.

PyTorch concepts: Dataset, DataLoader, custom collate_fn, padding masks, Dict[str, Tensor].
"""
import logging
import math
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
from torch.utils.data import Dataset

from .neural_reranker import (
    MAX_SIMILAR_VIDEOS,
    NUM_CANDIDATE_NUMERIC,
    SIMILAR_VIDEO_DIM,
    TITLE_EMBEDDING_DIM,
    duration_to_bucket,
)

logger = logging.getLogger(__name__)

# Candidate numeric feature names (15 features) — order matters
CANDIDATE_NUMERIC_KEYS = [
    "yt_log_views", "yt_log_likes", "yt_log_comments",
    "yt_duration_seconds", "yt_like_view_ratio", "yt_comment_view_ratio",
    "publish_hour_sin", "publish_hour_cos", "publish_dow_sin", "publish_dow_cos",
    "title_length", "title_exclamation_count", "title_caps_ratio",
    "heat_score", "relevance_score",
]

assert len(CANDIDATE_NUMERIC_KEYS) == NUM_CANDIDATE_NUMERIC, (
    f"Expected {NUM_CANDIDATE_NUMERIC} candidate keys, got {len(CANDIDATE_NUMERIC_KEYS)}"
)


class PredictorDataset(Dataset):
    """Dataset for the neural predictor.

    Each sample contains:
        - candidate_numeric: [NUM_CANDIDATE_NUMERIC] float tensor
        - title_embedding: [TITLE_EMBEDDING_DIM] float tensor
        - category_id: int — YouTube category ID
        - duration_bucket: int — discretized duration
        - similar_videos: list of dicts with {log_views, similarity, rank}
        - target: float — log1p(bilibili_views)
        - channel_id: str — for GroupKFold
    """

    def __init__(self, samples: List[Dict]):
        """Initialize dataset from a list of sample dicts.

        Each sample dict must have:
            - "candidate": dict with candidate feature values
            - "title_embedding": list/array of TITLE_EMBEDDING_DIM floats (or None)
            - "similar_videos": list of dicts (variable length)
            - "target": float (log1p views)
            - "channel_id": str
            - "category_id": int
            - "duration_seconds": int
        """
        self.samples = samples

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> Dict[str, object]:
        sample = self.samples[idx]

        # Candidate numeric features
        candidate = sample["candidate"]
        candidate_numeric = torch.tensor(
            [candidate.get(k, 0.0) for k in CANDIDATE_NUMERIC_KEYS],
            dtype=torch.float32,
        )

        # Title embedding (zeros if not available)
        title_emb = sample.get("title_embedding")
        if title_emb is not None:
            title_embedding = torch.tensor(title_emb, dtype=torch.float32)
        else:
            title_embedding = torch.zeros(TITLE_EMBEDDING_DIM, dtype=torch.float32)

        # Categorical
        category_id = int(sample.get("category_id", 0)) % 50
        duration_bucket = duration_to_bucket(sample.get("duration_seconds", 0))

        # Similar videos (variable length, will be padded in collate_fn)
        similar_vids = sample.get("similar_videos", [])
        similar_list = []
        for sv in similar_vids[:MAX_SIMILAR_VIDEOS]:
            similar_list.append([
                sv.get("log_views", 0.0),
                sv.get("similarity", 0.0),
                sv.get("rank", 0.0),
            ])

        target = sample.get("target", 0.0)

        return {
            "candidate_numeric": candidate_numeric,
            "title_embedding": title_embedding,
            "category_id": category_id,
            "duration_bucket": duration_bucket,
            "similar_videos": similar_list,
            "target": target,
            "channel_id": sample.get("channel_id", ""),
        }


def predictor_collate_fn(batch: List[Dict]) -> Dict[str, torch.Tensor]:
    """Custom collate function that handles variable-length similar video lists.

    Pads similar_videos to MAX_SIMILAR_VIDEOS and creates padding masks.

    Returns:
        Dict with:
            candidate_numeric: [B, NUM_CANDIDATE_NUMERIC]
            title_embedding: [B, TITLE_EMBEDDING_DIM]
            category_id: [B] long
            duration_bucket: [B] long
            similar_features: [B, MAX_SIMILAR, SIMILAR_VIDEO_DIM]
            similar_padding_mask: [B, MAX_SIMILAR] bool (True = padded)
            target: [B] float
    """
    batch_size = len(batch)

    candidate_numeric = torch.stack([b["candidate_numeric"] for b in batch])
    title_embedding = torch.stack([b["title_embedding"] for b in batch])
    category_id = torch.tensor([b["category_id"] for b in batch], dtype=torch.long)
    duration_bucket = torch.tensor([b["duration_bucket"] for b in batch], dtype=torch.long)
    target = torch.tensor([b["target"] for b in batch], dtype=torch.float32)

    # Pad similar videos
    similar_features = torch.zeros(batch_size, MAX_SIMILAR_VIDEOS, SIMILAR_VIDEO_DIM)
    similar_padding_mask = torch.ones(batch_size, MAX_SIMILAR_VIDEOS, dtype=torch.bool)

    for i, b in enumerate(batch):
        sv = b["similar_videos"]
        n = min(len(sv), MAX_SIMILAR_VIDEOS)
        if n > 0:
            similar_features[i, :n] = torch.tensor(sv[:n], dtype=torch.float32)
            similar_padding_mask[i, :n] = False

    return {
        "candidate_numeric": candidate_numeric,
        "title_embedding": title_embedding,
        "category_id": category_id,
        "duration_bucket": duration_bucket,
        "similar_features": similar_features,
        "similar_padding_mask": similar_padding_mask,
        "target": target,
    }


def build_samples_from_db(
    videos,
    targets: np.ndarray,
    feature_dicts: List[Dict[str, float]],
    title_embeddings: Optional[np.ndarray] = None,
    similar_videos_list: Optional[List[List[Dict]]] = None,
) -> List[Dict]:
    """Build sample dicts from training data for PredictorDataset.

    Args:
        videos: List of CompetitorVideo.
        targets: Array of log1p(views) targets.
        feature_dicts: List of feature dicts from extract_features_single().
        title_embeddings: Optional [N, TITLE_EMBEDDING_DIM] array.
        similar_videos_list: Optional list of similar video lists per sample.

    Returns:
        List of sample dicts ready for PredictorDataset.
    """
    samples = []
    for i, (video, target) in enumerate(zip(videos, targets)):
        feat = feature_dicts[i]

        # Build candidate dict from feature values
        candidate = {k: feat.get(k, 0.0) for k in CANDIDATE_NUMERIC_KEYS}

        # Title embedding
        title_emb = title_embeddings[i].tolist() if title_embeddings is not None else None

        # Similar videos
        sim_vids = similar_videos_list[i] if similar_videos_list else []

        samples.append({
            "candidate": candidate,
            "title_embedding": title_emb,
            "similar_videos": sim_vids,
            "target": float(target),
            "channel_id": video.bilibili_uid,
            "category_id": int(feat.get("yt_category_id", 0)),
            "duration_seconds": int(feat.get("duration", video.duration)),
        })

    return samples
