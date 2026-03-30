"""PyTorch nn.Module for title embedding with projection head.

Concepts: nn.Module, forward(), nn.Linear, nn.Sequential, nn.ReLU, nn.Dropout,
          requires_grad, state_dict, torch.no_grad.
"""
import logging
from typing import Dict, List, Optional

import numpy as np
import torch
import torch.nn as nn
from transformers import AutoModel, AutoTokenizer

logger = logging.getLogger(__name__)

DEFAULT_BACKBONE = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
BACKBONE_DIM = 384


class TitleEmbedder(nn.Module):
    """Embeds video titles into a dense vector via a transformer backbone + projection.

    Architecture:
        backbone (frozen or fine-tuned) -> mean pooling -> projection head -> [batch, projection_dim]
    """

    def __init__(
        self,
        backbone_name: str = DEFAULT_BACKBONE,
        projection_dim: int = 128,
        freeze_backbone: bool = True,
        dropout: float = 0.1,
    ):
        super().__init__()
        self.backbone_name = backbone_name
        self.projection_dim = projection_dim
        self.freeze_backbone = freeze_backbone

        self.backbone = AutoModel.from_pretrained(backbone_name)
        self._tokenizer = AutoTokenizer.from_pretrained(backbone_name)

        if freeze_backbone:
            for param in self.backbone.parameters():
                param.requires_grad = False

        self.projection = nn.Sequential(
            nn.Linear(BACKBONE_DIM, projection_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(projection_dim, projection_dim),
        )

    def forward(self, input_ids: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
        """Forward pass: backbone -> mean pool -> project.

        Args:
            input_ids: Token IDs, shape [batch, seq_len].
            attention_mask: Attention mask, shape [batch, seq_len].

        Returns:
            Projected embeddings, shape [batch, projection_dim].
        """
        if self.freeze_backbone:
            with torch.no_grad():
                backbone_out = self.backbone(input_ids=input_ids, attention_mask=attention_mask)
            hidden = backbone_out.last_hidden_state.detach().to(attention_mask.device)
        else:
            backbone_out = self.backbone(input_ids=input_ids, attention_mask=attention_mask)
            hidden = backbone_out.last_hidden_state

        # Mean pooling over non-padded tokens
        mask_expanded = attention_mask.unsqueeze(-1).float()
        summed = (hidden * mask_expanded).sum(dim=1)
        counts = mask_expanded.sum(dim=1).clamp(min=1e-9)
        pooled = summed / counts

        return self.projection(pooled)

    @torch.no_grad()
    def encode(self, titles: List[str], batch_size: int = 64) -> np.ndarray:
        """Encode a list of title strings into numpy embeddings.

        Args:
            titles: List of title strings.
            batch_size: Batch size for encoding.

        Returns:
            Numpy array of shape [N, projection_dim].
        """
        self.eval()
        all_embeddings = []

        for i in range(0, len(titles), batch_size):
            batch_titles = titles[i : i + batch_size]
            encoding = self._tokenizer(
                batch_titles,
                max_length=128,
                padding=True,
                truncation=True,
                return_tensors="pt",
            )
            device = next(self.parameters()).device
            input_ids = encoding["input_ids"].to(device)
            attention_mask = encoding["attention_mask"].to(device)
            emb = self.forward(input_ids, attention_mask)
            all_embeddings.append(emb.cpu().numpy())

        return np.concatenate(all_embeddings, axis=0)

    def get_config(self) -> Dict:
        """Return config dict for serialization."""
        return {
            "backbone_name": self.backbone_name,
            "projection_dim": self.projection_dim,
            "freeze_backbone": self.freeze_backbone,
        }

    def save(self, path: str) -> None:
        """Save model state_dict and config to a file."""
        torch.save({
            "config": self.get_config(),
            "state_dict": self.state_dict(),
        }, path)
        logger.info("Saved TitleEmbedder to %s", path)

    @classmethod
    def load(cls, path: str, device: Optional[str] = None) -> "TitleEmbedder":
        """Load a saved TitleEmbedder from file.

        Args:
            path: Path to saved .pt file.
            device: Device to load onto (default: cpu).

        Returns:
            Loaded TitleEmbedder instance.
        """
        map_location = device or "cpu"
        checkpoint = torch.load(path, map_location=map_location, weights_only=False)
        config = checkpoint["config"]
        model = cls(
            backbone_name=config["backbone_name"],
            projection_dim=config["projection_dim"],
            freeze_backbone=config["freeze_backbone"],
        )
        model.load_state_dict(checkpoint["state_dict"])
        if device:
            model.to(device)
        return model
