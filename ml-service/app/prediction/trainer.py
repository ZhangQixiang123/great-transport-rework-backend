"""Training loop for the neural predictor.

PyTorch concepts: training loop (zero_grad/backward/step), AdamW, OneCycleLR,
torch.amp (mixed precision), GradScaler, clip_grad_norm_, model.train()/eval(),
torch.inference_mode(), early stopping, checkpointing, GroupKFold.
"""
import logging
import math
import os
import time
from typing import Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Subset

from ..db.database import Database
from ..embeddings.vector_store import VectorStore
from ..training.features import (
    FEATURE_NAMES,
    extract_features_single,
    load_regression_data,
    compute_yt_imputation_stats,
)
from .dataset import (
    PredictorDataset,
    build_samples_from_db,
    predictor_collate_fn,
)
from .neural_reranker import NeuralPredictor

logger = logging.getLogger(__name__)


def _get_device() -> str:
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _compute_metrics(
    predictions: np.ndarray, targets: np.ndarray,
) -> Dict[str, float]:
    """Compute evaluation metrics."""
    residuals = predictions - targets
    mse = float(np.mean(residuals ** 2))
    rmse = math.sqrt(mse)
    ss_res = float(np.sum(residuals ** 2))
    ss_tot = float(np.sum((targets - np.mean(targets)) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    mae = float(np.mean(np.abs(residuals)))

    if len(predictions) > 1 and np.std(predictions) > 1e-9 and np.std(targets) > 1e-9:
        correlation = float(np.corrcoef(predictions, targets)[0, 1])
    else:
        correlation = 0.0

    within_1 = float(np.mean(np.abs(residuals) < 1.0))

    return {
        "rmse": rmse,
        "r2": r2,
        "mae": mae,
        "correlation": correlation,
        "within_1_log": within_1,
    }


def _build_similar_videos_from_vector_store(
    videos,
    vector_store: VectorStore,
    embedder,
) -> Tuple[List[List[Dict]], np.ndarray]:
    """Generate similar video lists and title embeddings from VectorStore.

    Returns:
        Tuple of (similar_videos_list, title_embeddings [N, dim]).
    """
    titles = [v.title for v in videos]

    # Encode titles with the embedder
    embeddings = embedder.encode(titles)

    similar_videos_list = []
    for i, video in enumerate(videos):
        emb = embeddings[i]
        detailed = vector_store.query_detailed(
            emb,
            top_k=20,
            exclude_bvid=video.bvid,
            exclude_channel=video.bilibili_uid,
        )
        sim_vids = []
        for sv in detailed:
            sim_vids.append({
                "log_views": sv["log_views"],
                "similarity": sv["similarity"],
                "rank": float(sv["rank"]),
            })
        similar_videos_list.append(sim_vids)

    return similar_videos_list, embeddings


def train_predictor(
    db: Database,
    model_dir: str = "models",
    epochs: int = 50,
    batch_size: int = 256,
    lr: float = 1e-3,
    patience: int = 8,
    n_folds: int = 5,
    candidate_hidden: int = 128,
    similar_hidden: int = 64,
) -> Tuple[Optional[NeuralPredictor], Optional[Dict]]:
    """Train the neural predictor with GroupKFold cross-validation.

    Args:
        db: Database connection.
        model_dir: Directory to save model artifacts.
        epochs: Maximum training epochs per fold.
        batch_size: Training batch size.
        lr: Learning rate.
        patience: Early stopping patience.
        n_folds: Number of CV folds.
        candidate_hidden: Hidden dim for candidate encoder.
        similar_hidden: Hidden dim for similar video encoder.

    Returns:
        Tuple of (model, metrics_dict) or (None, None) on failure.
    """
    device = _get_device()
    use_amp = device == "cuda"
    logger.info("Training neural predictor on %s (AMP=%s)", device, use_amp)

    # ── Load data ──
    videos, targets, yt_stats_map = load_regression_data(db)
    if len(videos) < 50:
        logger.error("Not enough videos for predictor training: %d", len(videos))
        return None, None

    logger.info("Loaded %d videos for predictor training", len(videos))

    # Compute imputation stats and feature dicts
    yt_imputation = compute_yt_imputation_stats(videos, yt_stats_map)
    feature_dicts = []
    for v in videos:
        yt = yt_stats_map.get(v.bvid)
        yt_imputed = False
        if yt is None:
            ch_imp = yt_imputation.get("per_channel", {}).get(v.bilibili_uid)
            if ch_imp:
                yt = dict(ch_imp)
            else:
                yt = dict(yt_imputation.get("global", {}))
            if yt:
                yt_imputed = True
        feature_dicts.append(extract_features_single(v, yt_stats=yt, yt_imputed=yt_imputed))

    # ── Load VectorStore + embedder for real similar video features ──
    similar_videos_list = None
    title_embeddings = None
    vector_store_path = os.path.join(model_dir, "vector_store.npz")
    embedder_path = os.path.join(model_dir, "embedder.pt")

    if os.path.exists(vector_store_path) and os.path.exists(embedder_path):
        try:
            from ..embeddings.model import TitleEmbedder
            vector_store = VectorStore.load(vector_store_path)
            embedder = TitleEmbedder.load(embedder_path, device=device)
            similar_videos_list, title_embeddings = _build_similar_videos_from_vector_store(
                videos, vector_store, embedder,
            )
            logger.info("Built similar video lists and title embeddings from VectorStore")
        except Exception as e:
            logger.warning("Could not load VectorStore/embedder: %s", e)
    else:
        logger.warning(
            "VectorStore or embedder not found in %s. "
            "Training without similar video and title embedding features.", model_dir,
        )

    # Build dataset samples
    samples = build_samples_from_db(
        videos, targets, feature_dicts,
        title_embeddings=title_embeddings,
        similar_videos_list=similar_videos_list,
    )

    # ── GroupKFold by channel ──
    from sklearn.model_selection import GroupKFold

    channel_ids = [s["channel_id"] for s in samples]
    unique_channels = list(set(channel_ids))
    n_folds = min(n_folds, len(unique_channels))

    if n_folds < 2:
        logger.error("Need at least 2 unique channels for CV, got %d", len(unique_channels))
        return None, None

    gkf = GroupKFold(n_splits=n_folds)
    indices = np.arange(len(samples))

    fold_metrics = []
    best_global_model = None
    best_global_val_loss = float("inf")

    for fold_idx, (train_idx, val_idx) in enumerate(gkf.split(indices, groups=channel_ids)):
        logger.info("── Fold %d/%d (train=%d, val=%d) ──", fold_idx + 1, n_folds, len(train_idx), len(val_idx))

        train_samples = [samples[i] for i in train_idx]
        val_samples = [samples[i] for i in val_idx]

        train_dataset = PredictorDataset(train_samples)
        val_dataset = PredictorDataset(val_samples)

        pin = device == "cuda"
        train_loader = DataLoader(
            train_dataset, batch_size=batch_size, shuffle=True,
            collate_fn=predictor_collate_fn, drop_last=False,
            pin_memory=pin, num_workers=2,
        )
        val_loader = DataLoader(
            val_dataset, batch_size=batch_size, shuffle=False,
            collate_fn=predictor_collate_fn,
            pin_memory=pin, num_workers=2,
        )

        # Create model
        model = NeuralPredictor(
            candidate_hidden=candidate_hidden,
            similar_hidden=similar_hidden,
        ).to(device)
        if device == "cuda":
            model = torch.compile(model)

        optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.01)

        total_steps = epochs * len(train_loader)
        scheduler = torch.optim.lr_scheduler.OneCycleLR(
            optimizer, max_lr=lr, total_steps=max(total_steps, 1),
        )

        scaler = torch.amp.GradScaler(device) if use_amp else None
        criterion = nn.MSELoss()

        best_val_loss = float("inf")
        patience_counter = 0
        best_state = None

        for epoch in range(epochs):
            # ── Train ──
            model.train()
            train_loss_sum = 0.0
            train_count = 0

            for batch in train_loader:
                batch = {k: v.to(device) if isinstance(v, torch.Tensor) else v for k, v in batch.items()}

                optimizer.zero_grad()

                with torch.amp.autocast(device, enabled=use_amp):
                    preds = model(
                        batch["candidate_numeric"],
                        batch["title_embedding"],
                        batch["category_id"],
                        batch["duration_bucket"],
                        batch["similar_features"],
                        batch["similar_padding_mask"],
                    ).squeeze(-1)
                    loss = criterion(preds, batch["target"])

                if scaler is not None:
                    scaler.scale(loss).backward()
                    scaler.unscale_(optimizer)
                    torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                    scaler.step(optimizer)
                    scaler.update()
                else:
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                    optimizer.step()

                scheduler.step()
                loss_val = loss.item()
                if not math.isnan(loss_val):
                    train_loss_sum += loss_val * len(batch["target"])
                    train_count += len(batch["target"])

            train_loss = train_loss_sum / max(train_count, 1)

            # ── Validate ──
            model.eval()
            val_preds_all = []
            val_targets_all = []

            with torch.inference_mode():
                for batch in val_loader:
                    batch = {k: v.to(device) if isinstance(v, torch.Tensor) else v for k, v in batch.items()}
                    preds = model(
                        batch["candidate_numeric"],
                        batch["title_embedding"],
                        batch["category_id"],
                        batch["duration_bucket"],
                        batch["similar_features"],
                        batch["similar_padding_mask"],
                    ).squeeze(-1)
                    val_preds_all.append(preds.cpu().numpy())
                    val_targets_all.append(batch["target"].cpu().numpy())

            val_preds = np.concatenate(val_preds_all)
            val_targets = np.concatenate(val_targets_all)
            val_loss = float(np.mean((val_preds - val_targets) ** 2))

            if epoch % 5 == 0 or epoch == epochs - 1:
                metrics = _compute_metrics(val_preds, val_targets)
                logger.info(
                    "  Epoch %d: train_loss=%.4f, val_loss=%.4f, "
                    "r2=%.3f, corr=%.3f, within_1=%.2f",
                    epoch, train_loss, val_loss,
                    metrics["r2"], metrics["correlation"], metrics["within_1_log"],
                )

            # Early stopping
            if val_loss < best_val_loss - 1e-4:
                best_val_loss = val_loss
                patience_counter = 0
                best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            else:
                patience_counter += 1
                if patience_counter >= patience:
                    logger.info("  Early stopping at epoch %d", epoch)
                    break

        # Restore best model and evaluate
        if best_state is not None:
            model.load_state_dict(best_state)
        model.eval()

        all_val_preds = []
        all_val_targets = []
        with torch.inference_mode():
            for batch in val_loader:
                batch = {k: v.to(device) if isinstance(v, torch.Tensor) else v for k, v in batch.items()}
                preds = model(
                    batch["candidate_numeric"],
                    batch["title_embedding"],
                    batch["category_id"],
                    batch["duration_bucket"],
                    batch["similar_features"],
                    batch["similar_padding_mask"],
                ).squeeze(-1)
                all_val_preds.append(preds.cpu().numpy())
                all_val_targets.append(batch["target"].cpu().numpy())

        val_preds = np.concatenate(all_val_preds)
        val_targets = np.concatenate(all_val_targets)
        fold_result = _compute_metrics(val_preds, val_targets)
        fold_result["best_val_loss"] = best_val_loss
        fold_metrics.append(fold_result)

        logger.info(
            "  Fold %d result: r2=%.3f, corr=%.3f, rmse=%.3f, within_1=%.2f",
            fold_idx + 1, fold_result["r2"], fold_result["correlation"],
            fold_result["rmse"], fold_result["within_1_log"],
        )

        # Track best overall model
        if best_val_loss < best_global_val_loss:
            best_global_val_loss = best_val_loss
            best_global_model = {k: v.cpu().clone() for k, v in model.state_dict().items()}

    # ── Aggregate CV results ──
    mean_metrics = {}
    for key in fold_metrics[0]:
        vals = [fm[key] for fm in fold_metrics]
        mean_metrics[f"mean_{key}"] = float(np.mean(vals))
        mean_metrics[f"std_{key}"] = float(np.std(vals))

    logger.info(
        "CV results: mean_r2=%.3f (+/- %.3f), mean_corr=%.3f (+/- %.3f)",
        mean_metrics["mean_r2"], mean_metrics["std_r2"],
        mean_metrics["mean_correlation"], mean_metrics["std_correlation"],
    )

    # ── Train final model on all data ──
    logger.info("Training final model on all %d samples...", len(samples))
    final_model = NeuralPredictor(
        candidate_hidden=candidate_hidden,
        similar_hidden=similar_hidden,
    ).to(device)
    if device == "cuda":
        final_model = torch.compile(final_model)

    full_dataset = PredictorDataset(samples)
    full_loader = DataLoader(
        full_dataset, batch_size=batch_size, shuffle=True,
        collate_fn=predictor_collate_fn,
        pin_memory=(device == "cuda"), num_workers=2,
    )

    optimizer = torch.optim.AdamW(final_model.parameters(), lr=lr, weight_decay=0.01)
    total_steps = epochs * len(full_loader)
    scheduler = torch.optim.lr_scheduler.OneCycleLR(
        optimizer, max_lr=lr, total_steps=max(total_steps, 1),
    )
    scaler = torch.amp.GradScaler(device) if use_amp else None
    criterion = nn.MSELoss()

    for epoch in range(epochs):
        final_model.train()
        for batch in full_loader:
            batch = {k: v.to(device) if isinstance(v, torch.Tensor) else v for k, v in batch.items()}
            optimizer.zero_grad()

            with torch.amp.autocast(device, enabled=use_amp):
                preds = final_model(
                    batch["candidate_numeric"], batch["title_embedding"],
                    batch["category_id"], batch["duration_bucket"],
                    batch["similar_features"], batch["similar_padding_mask"],
                ).squeeze(-1)
                loss = criterion(preds, batch["target"])

            if scaler is not None:
                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(final_model.parameters(), max_norm=1.0)
                scaler.step(optimizer)
                scaler.update()
            else:
                loss.backward()
                torch.nn.utils.clip_grad_norm_(final_model.parameters(), max_norm=1.0)
                optimizer.step()

            scheduler.step()

    # Save (unwrap torch.compile wrapper if present)
    os.makedirs(model_dir, exist_ok=True)
    save_path = os.path.join(model_dir, "predictor.pt")
    raw_model = getattr(final_model, "_orig_mod", final_model)
    raw_model.cpu()
    raw_model.save(save_path)

    result_metrics = {
        "training_samples": len(samples),
        "unique_channels": len(unique_channels),
        "n_folds": n_folds,
        "epochs": epochs,
        "device": device,
        "cv_evaluation": mean_metrics,
    }

    logger.info("Neural predictor training complete. Saved to %s", save_path)
    return raw_model, result_metrics
