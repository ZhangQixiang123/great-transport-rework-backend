"""
Training pipeline for competitor video scoring.

Supports two modes:
  - Mixed effects (GPBoost): tree-boosted fixed effects + per-channel random intercepts.
    Best for known channels. For unseen channels, random effect = 0.
  - Pure fixed effects (LightGBM): no random intercepts.
    Better for cross-channel generalization (predicting unseen channels).

GroupKFold cross-validation by channel gives honest cross-channel metrics.
"""
import json
import logging
import math
import os
import shutil
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import joblib

import gpboost as gpb
import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold

from .evaluator import RegressionReport, evaluate_regression, evaluate_regression_gpb, evaluate_regression_simple
from .features import (
    FEATURE_NAMES, LABEL_MAP,
    compute_yt_imputation_stats,
    extract_features_dataframe, load_embedding_map,
    load_regression_data,
)
from ..db.database import CompetitorVideo, Database
from ..embeddings.model import TitleEmbedder
from ..embeddings.vector_store import VectorStore

logger = logging.getLogger(__name__)

def _load_embedder_and_store(
    model_dir: str,
    videos: List[CompetitorVideo],
    raw_targets: np.ndarray,
    batch_size: int = 64,
) -> tuple:
    """Try to load fine-tuned embedder, compute embeddings, and build VectorStore.

    Returns:
        Tuple of (embedder, fine_tuned_embeddings, vector_store).
        All None if embedder not found.
    """
    embedder_path = os.path.join(model_dir, "embedder.pt")
    if not os.path.exists(embedder_path):
        logger.info("No fine-tuned embedder found at %s", embedder_path)
        return None, None, None

    try:
        embedder = TitleEmbedder.load(embedder_path)
        logger.info("Loaded fine-tuned embedder from %s", embedder_path)

        all_titles = [v.title for v in videos]
        fine_embs = embedder.encode(all_titles, batch_size=batch_size)
        logger.info("Encoded %d titles -> shape %s", len(all_titles), fine_embs.shape)

        store = VectorStore()
        all_bvids = [v.bvid for v in videos]
        all_channels = [v.bilibili_uid for v in videos]
        store.build(fine_embs, all_bvids, raw_targets, all_channels)

        return embedder, fine_embs, store
    except Exception:
        logger.warning("Failed to load embedder or build VectorStore", exc_info=True)
        return None, None, None


def _compute_rag_features(
    store: VectorStore,
    embeddings: np.ndarray,
    videos: List[CompetitorVideo],
    exclude_channels: Optional[set] = None,
) -> List[Dict]:
    """Compute RAG features for each video using the VectorStore.

    Args:
        store: Built VectorStore.
        embeddings: Fine-tuned embeddings, shape [N, dim].
        videos: List of videos (same order as embeddings).
        exclude_channels: If set, exclude these channels from retrieval
                          (for cross-validation leakage prevention).

    Returns:
        List of RAG feature dicts, one per video.
    """
    rag_list = []
    for i, v in enumerate(videos):
        exclude_ch = v.bilibili_uid if exclude_channels and v.bilibili_uid in exclude_channels else None
        rag = store.query(
            embeddings[i],
            exclude_bvid=v.bvid,
            exclude_channel=exclude_ch,
        )
        rag_list.append(rag)
    return rag_list


DEFAULT_PARAMS = {
    "objective": "regression_l2",
    "learning_rate": 0.05,
    "num_leaves": 63,
    "feature_fraction": 0.8,
    "min_data_in_leaf": 20,
    "verbose": -1,
}


def train_model(
    db: Database,
    model_dir: str = "models",
    num_rounds: int = 500,
    learning_rate: Optional[float] = None,
    min_samples: int = 50,
    extra_params: Optional[Dict] = None,
    n_cv_folds: int = 5,
    cv_rounds: int = 300,
    embeddings_path: str = "models/title_embeddings.npz",
    use_random_intercepts: bool = True,
) -> Tuple[Optional[gpb.Booster], Optional[RegressionReport], Dict]:
    """Train a model to predict log(views).

    Args:
        db: Connected Database instance.
        model_dir: Directory to save model artifacts.
        num_rounds: Max boosting rounds for final model (default 500).
        learning_rate: Override default learning rate.
        min_samples: Minimum required samples.
        extra_params: Additional GPBoost params to merge.
        n_cv_folds: Number of cross-validation folds (default 5).
        cv_rounds: Fixed boosting rounds for CV folds (default 300).
        embeddings_path: Path to pre-computed title embeddings .npz.
        use_random_intercepts: If True (default), use GPBoost mixed effects
            with per-channel random intercepts. If False, use pure LightGBM
            (better for predicting unseen channels).

    Returns:
        Tuple of (model, regression_report, metadata_dict).
        model and report are None if insufficient data.
    """
    # Load raw data
    logger.info("Loading regression training data...")
    videos, raw_targets, yt_stats_map = load_regression_data(db)

    total = len(raw_targets)
    logger.info("Total samples: %d", total)

    if total < min_samples:
        logger.error("Need at least %d samples, got %d", min_samples, total)
        return None, None, {"error": f"Insufficient data: {total} < {min_samples}"}

    has_yt = sum(1 for v in videos if v.bvid in yt_stats_map)
    logger.info("Samples with YouTube stats: %d/%d (%.1f%%)", has_yt, total, has_yt / total * 100)

    unique_channels = set(v.bilibili_uid for v in videos)
    n_channels = len(unique_channels)
    logger.info("Unique channels: %d", n_channels)

    # Load embeddings
    embedding_map = load_embedding_map(embeddings_path)
    has_emb = sum(1 for v in videos if v.bvid in embedding_map)
    logger.info("Samples with title embeddings: %d/%d", has_emb, total)

    # Try to load fine-tuned embedder and build VectorStore
    embedder, fine_embs, vector_store = _load_embedder_and_store(
        model_dir, videos, raw_targets,
    )
    has_rag = embedder is not None
    if has_rag:
        logger.info("Fine-tuned embedder loaded, RAG features enabled")
        # Regenerate PCA embedding_map from fine-tuned embeddings
        from sklearn.decomposition import PCA
        from .features import N_EMBEDDING_DIMS
        pca = PCA(n_components=N_EMBEDDING_DIMS)
        pca_embs = pca.fit_transform(fine_embs)
        logger.info("PCA variance explained: %.3f", sum(pca.explained_variance_ratio_))
        pca_path = os.path.join(model_dir, "pca.pkl")
        joblib.dump(pca, pca_path)
        logger.info("PCA saved to %s", pca_path)
        embedding_map = {v.bvid: pca_embs[i] for i, v in enumerate(videos)}
        has_emb = total
        logger.info("Regenerated PCA embeddings from fine-tuned model for all %d videos", total)

    # Build params
    params = DEFAULT_PARAMS.copy()
    if learning_rate is not None:
        params["learning_rate"] = learning_rate
    if extra_params:
        params.update(extra_params)

    # Group array for GPBoost
    groups = np.array([v.bilibili_uid for v in videos])

    # ---- Cross-validation with GroupKFold ----
    actual_folds = min(n_cv_folds, n_channels)
    cv_fold_metrics = []

    if actual_folds >= 2:
        logger.info("Running %d-fold GroupKFold cross-validation...", actual_folds)
        gkf = GroupKFold(n_splits=actual_folds)

        for fold_idx, (train_idx, test_idx) in enumerate(gkf.split(raw_targets, groups=groups)):
            train_videos = [videos[i] for i in train_idx]
            test_videos = [videos[i] for i in test_idx]
            train_targets = raw_targets[train_idx]
            test_targets = raw_targets[test_idx]
            train_groups = groups[train_idx]
            test_groups = groups[test_idx]

            # Imputation stats from training fold only
            fold_yt_imp = compute_yt_imputation_stats(train_videos, yt_stats_map)

            # Compute RAG features if available
            train_rag = None
            test_rag = None
            if has_rag and vector_store is not None and fine_embs is not None:
                test_channel_set = set(test_groups)
                train_rag = _compute_rag_features(
                    vector_store, fine_embs[train_idx], train_videos,
                    exclude_channels=None,
                )
                test_rag = _compute_rag_features(
                    vector_store, fine_embs[test_idx], test_videos,
                    exclude_channels=test_channel_set,
                )

            # Build feature matrices
            X_train_df = extract_features_dataframe(
                train_videos, yt_stats_map=yt_stats_map,
                yt_imputation_stats=fold_yt_imp,
                embedding_map=embedding_map,
                rag_features_list=train_rag,
            )
            X_test_df = extract_features_dataframe(
                test_videos, yt_stats_map=yt_stats_map,
                yt_imputation_stats=fold_yt_imp,
                embedding_map=embedding_map,
                rag_features_list=test_rag,
            )

            if use_random_intercepts:
                fold_gp = gpb.GPModel(group_data=train_groups, likelihood="gaussian")
                fold_data = gpb.Dataset(X_train_df.values, train_targets)
                fold_model = gpb.train(
                    params=params, train_set=fold_data,
                    gp_model=fold_gp, num_boost_round=cv_rounds,
                )
                pred_dict = fold_model.predict(
                    data=X_test_df.values, group_data_pred=test_groups,
                )
                y_pred = np.array(pred_dict["response_mean"])
            else:
                fold_data = lgb.Dataset(X_train_df.values, train_targets)
                fold_model = lgb.train(
                    params=params, train_set=fold_data,
                    num_boost_round=cv_rounds,
                )
                y_pred = fold_model.predict(X_test_df.values)

            fold_metrics = evaluate_regression_simple(y_pred, test_targets)
            fold_metrics["fold"] = fold_idx
            fold_metrics["train_size"] = len(train_idx)
            fold_metrics["test_size"] = len(test_idx)
            cv_fold_metrics.append(fold_metrics)

            logger.info(
                "Fold %d: RMSE=%.4f, R2=%.4f, MAE=%.4f (train=%d, test=%d)",
                fold_idx, fold_metrics["rmse"], fold_metrics["r2"],
                fold_metrics["mae"], len(train_idx), len(test_idx),
            )

        avg_cv = {
            "mean_rmse": float(np.mean([m["rmse"] for m in cv_fold_metrics])),
            "mean_r2": float(np.mean([m["r2"] for m in cv_fold_metrics])),
            "mean_mae": float(np.mean([m["mae"] for m in cv_fold_metrics])),
            "mean_correlation": float(np.mean([m["correlation"] for m in cv_fold_metrics])),
            "mean_within_1_log": float(np.mean([m["within_1_log"] for m in cv_fold_metrics])),
            "mean_within_2_log": float(np.mean([m["within_2_log"] for m in cv_fold_metrics])),
            "n_folds": actual_folds,
            "per_fold": cv_fold_metrics,
        }
        logger.info(
            "CV Average: RMSE=%.4f, R2=%.4f, MAE=%.4f, Correlation=%.4f",
            avg_cv["mean_rmse"], avg_cv["mean_r2"],
            avg_cv["mean_mae"], avg_cv["mean_correlation"],
        )
    else:
        logger.warning("Only %d channel(s) -- skipping cross-validation", n_channels)
        avg_cv = {"n_folds": 0, "per_fold": [], "mean_rmse": 0.0, "mean_r2": 0.0}

    # ---- Train final model on ALL data ----
    logger.info("Training final model on all %d samples...", total)

    final_yt_imp = compute_yt_imputation_stats(videos, yt_stats_map)

    # Compute RAG features for final training (no channel exclusion)
    final_rag = None
    if has_rag and vector_store is not None and fine_embs is not None:
        final_rag = _compute_rag_features(
            vector_store, fine_embs, videos, exclude_channels=None,
        )

    all_features = extract_features_dataframe(
        videos, yt_stats_map=yt_stats_map,
        yt_imputation_stats=final_yt_imp,
        embedding_map=embedding_map,
        rag_features_list=final_rag,
    )

    logger.info(
        "Target range: [%.2f, %.2f] (log scale) = [%.0f, %.0f] views",
        raw_targets.min(), raw_targets.max(),
        math.expm1(raw_targets.min()), math.expm1(raw_targets.max()),
    )

    if use_random_intercepts:
        # GPBoost mixed effects model
        final_gp = gpb.GPModel(group_data=groups, likelihood="gaussian")
        final_data = gpb.Dataset(all_features.values, raw_targets)

        logger.info("Training GPBoost (%d max rounds)...", num_rounds)
        model = gpb.train(
            params=params, train_set=final_data,
            gp_model=final_gp, num_boost_round=num_rounds,
        )

        pred_all = model.predict(data=all_features.values, group_data_pred=groups)
        y_pred_all = np.array(pred_all["response_mean"])

        report = evaluate_regression_gpb(
            model, all_features.values, raw_targets, list(FEATURE_NAMES), groups,
        )

        gp_summary = str(final_gp.summary())
        logger.info("GP Model:\n%s", gp_summary)
    else:
        # Pure LightGBM (no random intercepts)
        final_data = lgb.Dataset(all_features.values, raw_targets)

        logger.info("Training LightGBM (%d max rounds, no random intercepts)...", num_rounds)
        model = lgb.train(
            params=params, train_set=final_data,
            num_boost_round=num_rounds,
        )

        y_pred_all = model.predict(all_features.values)

        report = evaluate_regression(
            model, all_features.values, raw_targets, list(FEATURE_NAMES),
        )

        gp_summary = "N/A (pure LightGBM, no random intercepts)"

    logger.info("Training complete.")
    logger.info(
        "Train set: RMSE=%.4f, R2=%.4f, MAE=%.4f",
        report.rmse, report.r2, report.mae,
    )

    # Compute percentile thresholds from predictions on ALL data
    percentiles = {
        "p25": float(np.percentile(y_pred_all, 25)),
        "p75": float(np.percentile(y_pred_all, 75)),
        "p95": float(np.percentile(y_pred_all, 95)),
    }
    logger.info("Classification thresholds (log scale): %s", percentiles)
    logger.info("  failed:     pred < %.2f (< %.0f views)", percentiles["p25"], math.expm1(percentiles["p25"]))
    logger.info("  standard:   %.2f <= pred < %.2f", percentiles["p25"], percentiles["p75"])
    logger.info("  successful: %.2f <= pred < %.2f", percentiles["p75"], percentiles["p95"])
    logger.info("  viral:      pred >= %.2f (>= %.0f views)", percentiles["p95"], math.expm1(percentiles["p95"]))

    # Save VectorStore if available
    if has_rag and vector_store is not None:
        vs_path = os.path.join(model_dir, "vector_store.npz")
        vector_store.save(vs_path)
        logger.info("VectorStore saved to %s", vs_path)

    # Save model
    os.makedirs(model_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    model_path = os.path.join(model_dir, f"model_{timestamp}.json")
    meta_path = os.path.join(model_dir, f"model_{timestamp}_meta.json")

    model.save_model(model_path)
    logger.info("Model saved: %s", model_path)

    # Serialize imputation stats
    serializable_yt_imp = {"per_channel": {}, "global": {}}
    for uid, stats in final_yt_imp.get("per_channel", {}).items():
        serializable_yt_imp["per_channel"][uid] = {k: float(v) for k, v in stats.items()}
    for k, v in final_yt_imp.get("global", {}).items():
        serializable_yt_imp["global"][k] = float(v)

    metadata = {
        "timestamp": timestamp,
        "model_type": "gpboost_mixed_effects" if use_random_intercepts else "lightgbm",
        "model_path": model_path,
        "num_features": len(FEATURE_NAMES),
        "feature_names": list(FEATURE_NAMES),
        "use_random_intercepts": use_random_intercepts,
        "label_map": LABEL_MAP,
        "percentile_thresholds": percentiles,
        "yt_imputation_stats": serializable_yt_imp,
        "params": {k: v for k, v in params.items() if k != "verbose"},
        "num_boost_round": num_rounds,
        "training_samples": total,
        "samples_with_youtube": has_yt,
        "unique_channels": n_channels,
        "cv_evaluation": avg_cv,
        "evaluation": report.to_dict(),
        "gp_summary": gp_summary,
        "embeddings_path": embeddings_path,
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)
    logger.info("Metadata saved: %s", meta_path)

    # Copy as latest
    latest_model = os.path.join(model_dir, "latest_model.json")
    latest_meta = os.path.join(model_dir, "latest_model_meta.json")
    shutil.copy2(model_path, latest_model)
    shutil.copy2(meta_path, latest_meta)
    logger.info("Copied as latest_model.json / latest_model_meta.json")

    return model, report, metadata
