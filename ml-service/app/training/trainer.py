"""
LightGBM training pipeline for competitor video scoring.

Two modes:
  1. Regression (primary): predict log(views) from pre-upload + YouTube features.
  2. Classification (legacy): multiclass on label categories.

Supports GPU-accelerated training (RTX 4080 / OpenCL) with automatic
fallback to CPU if GPU is unavailable.
"""
import json
import logging
import math
import os
import shutil
from datetime import datetime
from typing import Dict, Optional, Tuple

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.model_selection import ShuffleSplit

from .evaluator import RegressionReport, evaluate_regression
from .features import FEATURE_NAMES, LABEL_MAP, load_regression_data
from ..db.database import Database

logger = logging.getLogger(__name__)

DEFAULT_REGRESSION_PARAMS = {
    "objective": "regression",
    "metric": "rmse",
    "num_leaves": 63,
    "learning_rate": 0.05,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 5,
    "min_child_samples": 20,
    "verbose": -1,
}

GPU_PARAMS = {
    "device": "gpu",
    "gpu_platform_id": 0,
    "gpu_device_id": 0,
}


def _detect_gpu() -> bool:
    """Check if LightGBM GPU training is available."""
    try:
        tiny_data = lgb.Dataset(
            np.array([[1.0, 2.0], [3.0, 4.0]]),
            label=np.array([0, 1]),
        )
        params = {
            "objective": "binary",
            "device": "gpu",
            "gpu_platform_id": 0,
            "gpu_device_id": 0,
            "verbose": -1,
            "num_iterations": 1,
        }
        lgb.train(params, tiny_data, num_boost_round=1)
        return True
    except Exception:
        return False


def train_model(
    db: Database,
    model_dir: str = "models",
    test_size: float = 0.2,
    num_rounds: int = 1000,
    learning_rate: Optional[float] = None,
    min_samples: int = 50,
    use_gpu: Optional[bool] = None,
    extra_params: Optional[Dict] = None,
) -> Tuple[Optional[lgb.Booster], Optional[RegressionReport], Dict]:
    """Train a LightGBM regression model to predict log(views).

    Args:
        db: Connected Database instance.
        model_dir: Directory to save model artifacts.
        test_size: Fraction of data for test set (default 0.2).
        num_rounds: Max boosting rounds (default 1000).
        learning_rate: Override default learning rate.
        min_samples: Minimum required samples.
        use_gpu: True=force GPU, False=force CPU, None=auto-detect.
        extra_params: Additional LightGBM params to merge.

    Returns:
        Tuple of (model, regression_report, metadata_dict).
        model and report are None if insufficient data.
    """
    # Load data
    logger.info("Loading regression training data...")
    features, targets, feature_names, videos = load_regression_data(db)

    total = len(targets)
    logger.info(f"Total samples: {total}")

    if total < min_samples:
        logger.error(f"Need at least {min_samples} samples, got {total}")
        return None, None, {"error": f"Insufficient data: {total} < {min_samples}"}

    # Count how many have YouTube stats (yt_log_views > 0)
    has_yt = int((features["yt_log_views"] > 0).sum())
    logger.info(f"Samples with YouTube stats: {has_yt}/{total} ({has_yt/total*100:.1f}%)")

    # Train/test split (random, not stratified since it's regression)
    splitter = ShuffleSplit(n_splits=1, test_size=test_size, random_state=42)
    train_idx, test_idx = next(splitter.split(features))

    X_train = features.iloc[train_idx].values
    X_test = features.iloc[test_idx].values
    y_train = targets[train_idx]
    y_test = targets[test_idx]

    logger.info(f"Train: {len(y_train)} samples, Test: {len(y_test)} samples")
    logger.info(f"Target range: [{y_train.min():.2f}, {y_train.max():.2f}] (log scale)")
    logger.info(f"  = [{math.expm1(y_train.min()):.0f}, {math.expm1(y_train.max()):.0f}] views")

    # Build params
    params = DEFAULT_REGRESSION_PARAMS.copy()
    if learning_rate is not None:
        params["learning_rate"] = learning_rate

    # GPU detection
    if use_gpu is None:
        logger.info("Auto-detecting GPU support...")
        use_gpu = _detect_gpu()

    if use_gpu:
        params.update(GPU_PARAMS)
        logger.info("GPU training enabled")
    else:
        params["num_threads"] = -1
        logger.info("CPU training (using all cores)")

    if extra_params:
        params.update(extra_params)

    # Create LightGBM datasets
    train_data = lgb.Dataset(X_train, label=y_train, feature_name=feature_names)
    test_data = lgb.Dataset(X_test, label=y_test, feature_name=feature_names, reference=train_data)

    # Train with early stopping
    logger.info(f"Training LightGBM regression ({num_rounds} max rounds, early stop 50)...")
    callbacks = [
        lgb.early_stopping(stopping_rounds=50),
        lgb.log_evaluation(period=50),
    ]

    model = lgb.train(
        params,
        train_data,
        num_boost_round=num_rounds,
        valid_sets=[test_data],
        valid_names=["test"],
        callbacks=callbacks,
    )

    logger.info(f"Training complete. Best iteration: {model.best_iteration}")

    # Evaluate
    logger.info("Evaluating model...")
    report = evaluate_regression(model, X_test, y_test, feature_names)
    logger.info(f"RMSE: {report.rmse:.4f}, R2: {report.r2:.4f}, MAE: {report.mae:.4f}")

    # Compute percentile thresholds from training data for classification
    all_predictions = model.predict(features.values)
    percentiles = {
        "p25": float(np.percentile(all_predictions, 25)),
        "p75": float(np.percentile(all_predictions, 75)),
        "p95": float(np.percentile(all_predictions, 95)),
    }
    logger.info(f"Classification thresholds (log scale): {percentiles}")
    logger.info(f"  failed:     pred < {percentiles['p25']:.2f} (< {math.expm1(percentiles['p25']):.0f} views)")
    logger.info(f"  standard:   {percentiles['p25']:.2f} <= pred < {percentiles['p75']:.2f}")
    logger.info(f"  successful: {percentiles['p75']:.2f} <= pred < {percentiles['p95']:.2f}")
    logger.info(f"  viral:      pred >= {percentiles['p95']:.2f} (>= {math.expm1(percentiles['p95']):.0f} views)")

    # Save model
    os.makedirs(model_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    model_path = os.path.join(model_dir, f"model_{timestamp}.txt")
    meta_path = os.path.join(model_dir, f"model_{timestamp}_meta.json")

    model.save_model(model_path)
    logger.info(f"Model saved: {model_path}")

    # Save metadata
    metadata = {
        "timestamp": timestamp,
        "model_type": "regression",
        "model_path": model_path,
        "num_features": len(feature_names),
        "feature_names": feature_names,
        "label_map": LABEL_MAP,
        "percentile_thresholds": percentiles,
        "params": {k: v for k, v in params.items() if k != "verbose"},
        "best_iteration": model.best_iteration,
        "training_samples": len(y_train),
        "test_samples": len(y_test),
        "samples_with_youtube": has_yt,
        "gpu_used": use_gpu,
        "evaluation": report.to_dict(),
    }
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)
    logger.info(f"Metadata saved: {meta_path}")

    # Copy as latest
    latest_model = os.path.join(model_dir, "latest_model.txt")
    latest_meta = os.path.join(model_dir, "latest_model_meta.json")
    shutil.copy2(model_path, latest_model)
    shutil.copy2(meta_path, latest_meta)
    logger.info("Copied as latest_model.txt / latest_model_meta.json")

    return model, report, metadata
