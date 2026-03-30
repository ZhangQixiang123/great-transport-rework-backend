"""Numpy-based vector store for similar-video retrieval (RAG).

Pure numpy — no PyTorch dependency. Uses cosine similarity for retrieval.
"""
import logging
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

RAG_FEATURE_KEYS = [
    "rag_similar_median_log_views",
    "rag_similar_mean_log_views",
    "rag_similar_std_log_views",
    "rag_similar_max_log_views",
    "rag_top5_mean_log_views",
]


class VectorStore:
    """Stores video embeddings and retrieves similar videos by cosine similarity.

    Designed for RAG: given a query embedding, find similar videos and return
    aggregate statistics about their view counts.
    """

    def __init__(self):
        self.embeddings: Optional[np.ndarray] = None  # [N, dim]
        self.norms: Optional[np.ndarray] = None       # [N]
        self.bvids: Optional[np.ndarray] = None       # [N]
        self.log_views: Optional[np.ndarray] = None   # [N]
        self.channel_ids: Optional[np.ndarray] = None  # [N]

    @property
    def size(self) -> int:
        return len(self.bvids) if self.bvids is not None else 0

    def build(
        self,
        embeddings: np.ndarray,
        bvids: List[str],
        log_views: np.ndarray,
        channel_ids: List[str],
    ) -> None:
        """Build the vector store from arrays.

        Args:
            embeddings: Shape [N, dim] embedding matrix.
            bvids: List of N video bvids.
            log_views: Shape [N] array of log1p(views) values.
            channel_ids: List of N channel UIDs.
        """
        self.embeddings = embeddings.astype(np.float32)
        self.norms = np.linalg.norm(self.embeddings, axis=1)
        self.bvids = np.array(bvids, dtype=object)
        self.log_views = log_views.astype(np.float32)
        self.channel_ids = np.array(channel_ids, dtype=object)
        logger.info("VectorStore built with %d entries, dim=%d", len(bvids), embeddings.shape[1])

    def query(
        self,
        query_emb: np.ndarray,
        top_k: int = 20,
        exclude_bvid: Optional[str] = None,
        exclude_channel: Optional[str] = None,
    ) -> Dict[str, float]:
        """Query similar videos and return RAG feature dict.

        Args:
            query_emb: Shape [dim] or [1, dim] query embedding.
            top_k: Number of similar videos to retrieve.
            exclude_bvid: Exclude this bvid from results (self-exclusion).
            exclude_channel: Exclude all videos from this channel (leakage prevention).

        Returns:
            Dict with 5 RAG feature keys, all defaulting to 0.0 if no results.
        """
        defaults = {k: 0.0 for k in RAG_FEATURE_KEYS}

        if self.embeddings is None or self.size == 0:
            return defaults

        query_emb = query_emb.flatten().astype(np.float32)
        query_norm = np.linalg.norm(query_emb)
        if query_norm < 1e-9:
            return defaults

        # Cosine similarity: dot(q, e) / (|q| * |e|)
        similarities = self.embeddings @ query_emb / (self.norms * query_norm + 1e-9)

        # Build exclusion mask
        mask = np.ones(self.size, dtype=bool)
        if exclude_bvid is not None:
            mask &= self.bvids != exclude_bvid
        if exclude_channel is not None:
            mask &= self.channel_ids != exclude_channel

        similarities[~mask] = -np.inf

        # Get top-k indices
        if mask.sum() == 0:
            return defaults

        k = min(top_k, int(mask.sum()))
        top_indices = np.argpartition(similarities, -k)[-k:]
        top_indices = top_indices[np.argsort(similarities[top_indices])[::-1]]

        top_views = self.log_views[top_indices]

        features = {
            "rag_similar_median_log_views": float(np.median(top_views)),
            "rag_similar_mean_log_views": float(np.mean(top_views)),
            "rag_similar_std_log_views": float(np.std(top_views)),
            "rag_similar_max_log_views": float(np.max(top_views)),
        }

        # Top-5 mean (or fewer if less than 5 available)
        top5_views = top_views[:min(5, len(top_views))]
        features["rag_top5_mean_log_views"] = float(np.mean(top5_views))

        return features

    def query_detailed(
        self,
        query_emb: np.ndarray,
        top_k: int = 20,
        exclude_bvid: Optional[str] = None,
        exclude_channel: Optional[str] = None,
    ) -> List[Dict]:
        """Query similar videos and return per-video details.

        Unlike `query()` which returns aggregate stats, this returns individual
        video data for each match — useful for the neural reranker.

        Args:
            query_emb: Shape [dim] or [1, dim] query embedding.
            top_k: Number of similar videos to retrieve.
            exclude_bvid: Exclude this bvid from results (self-exclusion).
            exclude_channel: Exclude all videos from this channel (leakage prevention).

        Returns:
            List of dicts with keys: bvid, log_views, similarity, rank.
        """
        if self.embeddings is None or self.size == 0:
            return []

        query_emb = query_emb.flatten().astype(np.float32)
        query_norm = np.linalg.norm(query_emb)
        if query_norm < 1e-9:
            return []

        similarities = self.embeddings @ query_emb / (self.norms * query_norm + 1e-9)

        mask = np.ones(self.size, dtype=bool)
        if exclude_bvid is not None:
            mask &= self.bvids != exclude_bvid
        if exclude_channel is not None:
            mask &= self.channel_ids != exclude_channel

        similarities[~mask] = -np.inf

        if mask.sum() == 0:
            return []

        k = min(top_k, int(mask.sum()))
        top_indices = np.argpartition(similarities, -k)[-k:]
        top_indices = top_indices[np.argsort(similarities[top_indices])[::-1]]

        results = []
        for rank, idx in enumerate(top_indices):
            results.append({
                "bvid": str(self.bvids[idx]),
                "log_views": float(self.log_views[idx]),
                "similarity": float(similarities[idx]),
                "rank": rank + 1,
            })
        return results

    def save(self, path: str) -> None:
        """Save vector store to .npz file."""
        if self.embeddings is None:
            raise ValueError("VectorStore is empty, nothing to save")
        np.savez(
            path,
            embeddings=self.embeddings,
            bvids=self.bvids,
            log_views=self.log_views,
            channel_ids=self.channel_ids,
        )
        logger.info("VectorStore saved to %s", path)

    @classmethod
    def load(cls, path: str) -> "VectorStore":
        """Load vector store from .npz file."""
        store = cls()
        data = np.load(path, allow_pickle=True)
        store.embeddings = data["embeddings"].astype(np.float32)
        store.norms = np.linalg.norm(store.embeddings, axis=1)
        store.bvids = data["bvids"]
        store.log_views = data["log_views"].astype(np.float32)
        store.channel_ids = data["channel_ids"]
        logger.info("VectorStore loaded from %s (%d entries)", path, store.size)
        return store
