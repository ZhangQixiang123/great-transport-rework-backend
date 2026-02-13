"""
Training pipeline for competitor video scoring model.

Uses GPBoost mixed effects model with tree-boosted fixed effects
and per-channel random intercepts.
"""
from .features import (
    extract_features_single,
    extract_features_dataframe,
    extract_labels,
    extract_regression_target,
    compute_yt_imputation_stats,
    load_embedding_map,
    load_regression_data,
    load_training_data,
    FEATURE_NAMES,
    PRE_UPLOAD_FEATURES,
    CLICKBAIT_FEATURES,
    YOUTUBE_FEATURES,
    ADDITIONAL_FEATURES,
    EMBEDDING_FEATURES,
)
from .data_validator import validate_training_data, ValidationResult
from .trainer import train_model
from .evaluator import (
    evaluate_model,
    evaluate_regression,
    evaluate_regression_gpb,
    evaluate_regression_simple,
    EvaluationReport,
    RegressionReport,
)

__all__ = [
    "extract_features_single",
    "extract_features_dataframe",
    "extract_labels",
    "extract_regression_target",
    "compute_yt_imputation_stats",
    "load_embedding_map",
    "load_regression_data",
    "load_training_data",
    "FEATURE_NAMES",
    "PRE_UPLOAD_FEATURES",
    "CLICKBAIT_FEATURES",
    "YOUTUBE_FEATURES",
    "ADDITIONAL_FEATURES",
    "EMBEDDING_FEATURES",
    "validate_training_data",
    "ValidationResult",
    "train_model",
    "evaluate_model",
    "evaluate_regression",
    "evaluate_regression_gpb",
    "evaluate_regression_simple",
    "EvaluationReport",
    "RegressionReport",
]
