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

logger = logging.getLogger(__name__)

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

            # Build feature matrices
            X_train_df = extract_features_dataframe(
                train_videos, yt_stats_map=yt_stats_map,
                yt_imputation_stats=fold_yt_imp,
                embedding_map=embedding_map,
            )
            X_test_df = extract_features_dataframe(
                test_videos, yt_stats_map=yt_stats_map,
                yt_imputation_stats=fold_yt_imp,
                embedding_map=embedding_map,
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

    all_features = extract_features_dataframe(
        videos, yt_stats_map=yt_stats_map,
        yt_imputation_stats=final_yt_imp,
        embedding_map=embedding_map,
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
