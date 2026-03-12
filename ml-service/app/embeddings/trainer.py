"""Fine-tuning training loop for TitleEmbedder.

Concepts: optimizer.zero_grad(), loss.backward(), optimizer.step(),
          model.train(), model.eval(), torch.no_grad(), ReduceLROnPlateau,
          early stopping, device management.
"""
import logging
import math
import os
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
from sklearn.model_selection import GroupKFold

from ..db.database import Database
from ..training.features import load_regression_data
from .dataset import VideoTitleDataset, create_dataloaders
from .model import TitleEmbedder
from .vector_store import VectorStore

logger = logging.getLogger(__name__)


class RegressionHead(nn.Module):
    """Auxiliary regression head for fine-tuning the embedder.

    Maps embeddings to scalar log(views) predictions.
    Discarded after training — only the embedder is kept.
    """

    def __init__(self, input_dim: int = 128, dropout: float = 0.1):
        super().__init__()
        self.head = nn.Sequential(
            nn.Linear(input_dim, 64),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(64, 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.head(x).squeeze(-1)


def fine_tune_embeddings(
    db: Database,
    model_dir: str = "models",
    epochs: int = 30,
    batch_size: int = 64,
    lr: float = 1e-3,
    patience: int = 5,
    freeze_backbone: bool = True,
    projection_dim: int = 128,
    backbone_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    backbone_lr: float = 2e-5,
) -> Tuple[Optional[TitleEmbedder], Optional[Dict]]:
    """Fine-tune a TitleEmbedder on video title -> log(views) regression.

    Uses GroupKFold by channel to select the best epoch, then retrains
    on all data with that many epochs.

    Args:
        db: Connected Database instance.
        model_dir: Directory to save embedder.pt.
        epochs: Maximum training epochs.
        batch_size: Batch size.
        lr: Learning rate.
        patience: Early stopping patience (epochs without improvement).
        freeze_backbone: If True, freeze the transformer backbone.
        projection_dim: Embedding dimension after projection.
        backbone_name: HuggingFace model name for the backbone.

    Returns:
        Tuple of (embedder, metrics_dict). Both None if insufficient data.
    """
    logger.info("Loading data for embedding fine-tuning...")
    videos, raw_targets, _ = load_regression_data(db)

    if len(videos) < 100:
        logger.warning("Need at least 100 videos for fine-tuning, got %d", len(videos))
        return None, None

    groups = np.array([v.bilibili_uid for v in videos])
    unique_channels = set(groups)
    n_channels = len(unique_channels)
    logger.info("Fine-tuning on %d videos from %d channels", len(videos), n_channels)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info("Using device: %s", device)

    # AMP setup: fp16 on CUDA for Tensor Core acceleration, no-op on CPU
    use_amp = (device == "cuda")
    scaler = torch.amp.GradScaler("cuda", enabled=use_amp) if use_amp else None

    # --- Validation split to pick best epoch ---
    if n_channels >= 3:
        n_folds = min(3, n_channels)
        gkf = GroupKFold(n_splits=n_folds)
        train_idx, val_idx = next(gkf.split(raw_targets, groups=groups))
        train_idx = list(train_idx)
        val_idx = list(val_idx)
        logger.info("Validation split: %d train, %d val", len(train_idx), len(val_idx))
    else:
        # Too few channels for group split — use 80/20 random
        n = len(videos)
        perm = np.random.permutation(n)
        split = int(0.8 * n)
        train_idx = perm[:split].tolist()
        val_idx = perm[split:].tolist()
        logger.info("Random split (few channels): %d train, %d val", len(train_idx), len(val_idx))

    train_loader, val_loader = create_dataloaders(
        videos, train_idx, val_idx,
        batch_size=batch_size, tokenizer_name=backbone_name,
        pin_memory=(device == "cuda"),
    )

    embedder = TitleEmbedder(
        backbone_name=backbone_name,
        projection_dim=projection_dim,
        freeze_backbone=freeze_backbone,
    ).to(device)

    reg_head = RegressionHead(input_dim=projection_dim).to(device)

    # Optimizer: use differential LR when backbone is unfrozen
    if freeze_backbone:
        all_params = list(embedder.projection.parameters()) + list(reg_head.parameters())
        optimizer = torch.optim.Adam(all_params, lr=lr)
    else:
        optimizer = torch.optim.Adam([
            {"params": embedder.backbone.parameters(), "lr": backbone_lr},
            {"params": embedder.projection.parameters(), "lr": lr},
            {"params": reg_head.parameters(), "lr": lr},
        ])
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5, patience=2,
    )
    criterion = nn.MSELoss()

    best_val_loss = float("inf")
    best_epoch = 0
    best_state = None
    epochs_without_improvement = 0

    for epoch in range(epochs):
        # --- Training ---
        embedder.train()
        reg_head.train()
        train_loss_sum = 0.0
        train_count = 0

        for batch in train_loader:
            input_ids = batch["input_ids"].to(device, non_blocking=True)
            attention_mask = batch["attention_mask"].to(device, non_blocking=True)
            targets = batch["target"].to(device, non_blocking=True)

            optimizer.zero_grad()
            with torch.amp.autocast("cuda", enabled=use_amp):
                emb = embedder(input_ids, attention_mask)
                pred = reg_head(emb)
                loss = criterion(pred, targets)

            if scaler is not None:
                scaler.scale(loss).backward()
                scaler.step(optimizer)
                scaler.update()
            else:
                loss.backward()
                optimizer.step()

            train_loss_sum += loss.item() * len(targets)
            train_count += len(targets)

        train_loss = train_loss_sum / max(train_count, 1)

        # --- Validation ---
        embedder.eval()
        reg_head.eval()
        val_loss_sum = 0.0
        val_count = 0

        with torch.no_grad():
            for batch in val_loader:
                input_ids = batch["input_ids"].to(device, non_blocking=True)
                attention_mask = batch["attention_mask"].to(device, non_blocking=True)
                targets = batch["target"].to(device, non_blocking=True)

                with torch.amp.autocast("cuda", enabled=use_amp):
                    emb = embedder(input_ids, attention_mask)
                    pred = reg_head(emb)
                    loss = criterion(pred, targets)

                val_loss_sum += loss.item() * len(targets)
                val_count += len(targets)

        val_loss = val_loss_sum / max(val_count, 1)
        scheduler.step(val_loss)

        current_lr = optimizer.param_groups[0]["lr"]
        logger.info(
            "Epoch %d/%d: train_loss=%.4f, val_loss=%.4f, lr=%.2e",
            epoch + 1, epochs, train_loss, val_loss, current_lr,
        )

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_epoch = epoch + 1
            best_state = {k: v.cpu().clone() for k, v in embedder.state_dict().items()}
            epochs_without_improvement = 0
        else:
            epochs_without_improvement += 1
            if epochs_without_improvement >= patience:
                logger.info("Early stopping at epoch %d (patience=%d)", epoch + 1, patience)
                break

    # Restore best embedder state
    if best_state is not None:
        embedder.load_state_dict(best_state)
    embedder.to(device)

    # Save
    os.makedirs(model_dir, exist_ok=True)
    embedder_path = os.path.join(model_dir, "embedder.pt")
    embedder.save(embedder_path)

    # Build vector store from all videos
    logger.info("Building vector store from %d videos...", len(videos))
    all_titles = [v.title for v in videos]
    all_embs = embedder.encode(all_titles, batch_size=batch_size)
    all_bvids = [v.bvid for v in videos]
    all_channels = [v.bilibili_uid for v in videos]

    vector_store = VectorStore()
    vector_store.build(all_embs, all_bvids, raw_targets, all_channels)
    vector_store_path = os.path.join(model_dir, "vector_store.npz")
    vector_store.save(vector_store_path)

    metrics = {
        "best_epoch": best_epoch,
        "best_val_loss": float(best_val_loss),
        "total_epochs": epoch + 1,
        "num_videos": len(videos),
        "num_channels": n_channels,
        "projection_dim": projection_dim,
        "freeze_backbone": freeze_backbone,
        "device": device,
        "vector_store_size": vector_store.size,
    }
    logger.info(
        "Fine-tuning complete: best_epoch=%d, best_val_loss=%.4f, vector_store=%d entries",
        best_epoch, best_val_loss, vector_store.size,
    )

    return embedder, metrics
