"""PyTorch neural predictor — combines candidate stats, title embedding, and similar videos.

Replaces the old NeuralReranker (which required LLM features at inference).
This model uses title embeddings from TitleEmbedder and VectorStore similar videos.

PyTorch concepts used:
    nn.Module, forward(), nn.Embedding, nn.MultiheadAttention,
    nn.TransformerEncoderLayer, nn.LayerNorm, residual connections, padding masks,
    nn.Sequential, nn.Linear, nn.ReLU, nn.GELU, nn.Dropout.
"""
import logging
import math
from typing import Dict, List, Optional

import torch
import torch.nn as nn

logger = logging.getLogger(__name__)

# ── Feature dimensions ──────────────────────────────────────────────────
NUM_CANDIDATE_NUMERIC = 15  # yt_log_views/likes/comments, duration, engagement, time, heat, relevance...
TITLE_EMBEDDING_DIM = 128   # from TitleEmbedder projection
SIMILAR_VIDEO_DIM = 3       # log_views, similarity, rank
MAX_SIMILAR_VIDEOS = 20

# Categorical feature dimensions
NUM_YT_CATEGORIES = 50      # YouTube has ~45 categories, pad to 50
NUM_DURATION_BUCKETS = 8    # <30s, 30s-2m, 2-5m, 5-10m, 10-20m, 20-60m, 60m+, unknown
EMBEDDING_DIM = 16


def duration_to_bucket(seconds: float) -> int:
    """Convert duration in seconds to a bucket index."""
    if seconds <= 0:
        return 7  # unknown
    elif seconds < 30:
        return 0
    elif seconds < 120:
        return 1
    elif seconds < 300:
        return 2
    elif seconds < 600:
        return 3
    elif seconds < 1200:
        return 4
    elif seconds < 3600:
        return 5
    else:
        return 6


class SimilarVideoEncoder(nn.Module):
    """Encodes a variable-length set of similar videos via self-attention.

    Takes [batch, max_similar, feature_dim] and a padding mask,
    applies a linear projection then multi-head self-attention,
    and returns [batch, hidden_dim] via mean pooling over non-padded positions.
    """

    def __init__(self, input_dim: int = SIMILAR_VIDEO_DIM, hidden_dim: int = 64, num_heads: int = 4):
        super().__init__()
        self.projection = nn.Linear(input_dim, hidden_dim)
        self.self_attention = nn.MultiheadAttention(
            embed_dim=hidden_dim,
            num_heads=num_heads,
            dropout=0.1,
            batch_first=True,
        )
        self.layer_norm = nn.LayerNorm(hidden_dim)

    def forward(
        self, similar_features: torch.Tensor, padding_mask: torch.Tensor,
    ) -> torch.Tensor:
        """Forward pass.

        Args:
            similar_features: [batch, max_similar, input_dim]
            padding_mask: [batch, max_similar] — True for padded positions.

        Returns:
            [batch, hidden_dim] pooled representation.
        """
        batch_size = similar_features.size(0)
        hidden_dim = self.projection.out_features

        # Check if any sample has all positions padded
        has_content = (~padding_mask).any(dim=1)  # [B]

        # Project to hidden dim
        x = self.projection(similar_features)  # [B, S, H]

        if has_content.any():
            safe_mask = padding_mask.clone()
            safe_mask[~has_content] = False

            attn_out, _ = self.self_attention(
                x, x, x, key_padding_mask=safe_mask,
            )
            x = self.layer_norm(x + attn_out)

        # Mean pooling over non-padded positions
        mask_expanded = (~padding_mask).unsqueeze(-1).float()  # [B, S, 1]
        summed = (x * mask_expanded).sum(dim=1)  # [B, H]
        counts = mask_expanded.sum(dim=1).clamp(min=1.0)  # [B, 1]
        result = summed / counts  # [B, H]

        # Zero out representations for fully-padded samples
        result[~has_content] = 0.0

        return result


class NeuralPredictor(nn.Module):
    """Neural predictor that combines candidate features, title embeddings, and similar videos.

    Architecture:
        1. Categorical embeddings (category_id, duration_bucket) -> [B, 2*emb_dim]
        2. Candidate encoder: numeric + title_embedding + categorical -> [B, candidate_hidden]
        3. SimilarVideoEncoder: self-attention over similar videos -> [B, similar_hidden]
        4. Cross-attention: candidate attends to similar video set
        5. Transformer refinement layer
        6. Prediction head -> [B, 1] predicted log(views)
    """

    def __init__(
        self,
        candidate_hidden: int = 128,
        similar_hidden: int = 64,
        num_heads: int = 4,
        dropout: float = 0.1,
        title_embedding_dim: int = TITLE_EMBEDDING_DIM,
    ):
        super().__init__()
        self.title_embedding_dim = title_embedding_dim

        # ── Categorical embeddings ──
        self.category_embedding = nn.Embedding(NUM_YT_CATEGORIES, EMBEDDING_DIM, padding_idx=0)
        self.duration_bucket_embedding = nn.Embedding(NUM_DURATION_BUCKETS, EMBEDDING_DIM)

        # ── Candidate encoder ──
        # Input: numeric features + title embedding + categorical embeddings
        candidate_input_dim = NUM_CANDIDATE_NUMERIC + title_embedding_dim + 2 * EMBEDDING_DIM
        self.input_norm = nn.LayerNorm(candidate_input_dim)
        self.candidate_encoder = nn.Sequential(
            nn.Linear(candidate_input_dim, candidate_hidden),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(candidate_hidden, candidate_hidden),
            nn.GELU(),
            nn.LayerNorm(candidate_hidden),
        )

        # ── Similar video encoder ──
        self.similar_encoder = SimilarVideoEncoder(
            input_dim=SIMILAR_VIDEO_DIM,
            hidden_dim=similar_hidden,
            num_heads=num_heads,
        )

        # ── Cross-attention: candidate attends to similar videos ──
        self.candidate_to_similar = nn.Linear(candidate_hidden, similar_hidden)
        self.cross_attention = nn.MultiheadAttention(
            embed_dim=similar_hidden,
            num_heads=num_heads,
            dropout=dropout,
            batch_first=True,
        )
        self.cross_norm = nn.LayerNorm(similar_hidden)

        # ── Transformer refinement ──
        combined_dim = candidate_hidden + similar_hidden
        self.refinement = nn.TransformerEncoderLayer(
            d_model=combined_dim,
            nhead=num_heads,
            dim_feedforward=combined_dim * 2,
            dropout=dropout,
            activation="gelu",
            batch_first=True,
        )

        # ── Prediction head ──
        self.prediction_head = nn.Sequential(
            nn.Linear(combined_dim, combined_dim // 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(combined_dim // 2, 1),
        )

    def forward(
        self,
        candidate_numeric: torch.Tensor,
        title_embedding: torch.Tensor,
        category_id: torch.Tensor,
        duration_bucket: torch.Tensor,
        similar_features: torch.Tensor,
        similar_padding_mask: torch.Tensor,
    ) -> torch.Tensor:
        """Forward pass.

        Args:
            candidate_numeric: [B, NUM_CANDIDATE_NUMERIC] float features.
            title_embedding: [B, TITLE_EMBEDDING_DIM] title embeddings.
            category_id: [B] long tensor — YouTube category IDs.
            duration_bucket: [B] long tensor — duration bucket indices.
            similar_features: [B, MAX_SIMILAR, SIMILAR_VIDEO_DIM] similar video features.
            similar_padding_mask: [B, MAX_SIMILAR] bool — True where padded.

        Returns:
            [B, 1] predicted log(views).
        """
        # 1. Categorical embeddings
        cat_emb = self.category_embedding(category_id)  # [B, emb_dim]
        dur_emb = self.duration_bucket_embedding(duration_bucket)  # [B, emb_dim]

        # 2. Candidate encoding
        candidate_input = torch.cat(
            [candidate_numeric, title_embedding, cat_emb, dur_emb], dim=-1,
        )
        candidate_input = self.input_norm(candidate_input)
        candidate_repr = self.candidate_encoder(candidate_input)  # [B, candidate_hidden]

        # 3. Similar video encoding (via self-attention)
        similar_repr = self.similar_encoder(
            similar_features, similar_padding_mask,
        )  # [B, similar_hidden]

        # 4. Cross-attention: candidate attends to the similar video set
        similar_projected = self.similar_encoder.projection(similar_features)
        candidate_query = self.candidate_to_similar(candidate_repr).unsqueeze(1)

        has_similar = (~similar_padding_mask).any(dim=1)
        safe_mask = similar_padding_mask.clone()
        safe_mask[~has_similar] = False

        cross_out, _ = self.cross_attention(
            candidate_query, similar_projected, similar_projected,
            key_padding_mask=safe_mask,
        )
        cross_out = cross_out.squeeze(1)
        cross_out[~has_similar] = 0.0

        cross_repr = self.cross_norm(similar_repr + cross_out)

        # 5. Combine and refine
        combined = torch.cat([candidate_repr, cross_repr], dim=-1)
        combined = combined.unsqueeze(1)
        refined = self.refinement(combined)
        refined = refined.squeeze(1)

        # 6. Predict
        return self.prediction_head(refined)

    def save(self, path: str) -> None:
        """Save model weights and config."""
        torch.save({
            "state_dict": self.state_dict(),
            "config": {
                "candidate_hidden_dim": self.candidate_encoder[0].out_features,
                "similar_hidden_dim": self.similar_encoder.projection.out_features,
                "title_embedding_dim": self.title_embedding_dim,
            },
        }, path)
        logger.info("NeuralPredictor saved to %s", path)

    @classmethod
    def load(cls, path: str, device: Optional[str] = None) -> "NeuralPredictor":
        """Load a saved NeuralPredictor."""
        map_location = device or "cpu"
        checkpoint = torch.load(path, map_location=map_location, weights_only=False)
        config = checkpoint.get("config", {})

        model = cls(
            candidate_hidden=config.get("candidate_hidden_dim", 128),
            similar_hidden=config.get("similar_hidden_dim", 64),
            title_embedding_dim=config.get("title_embedding_dim", TITLE_EMBEDDING_DIM),
        )
        model.load_state_dict(checkpoint["state_dict"])
        if device:
            model.to(device)
        return model
