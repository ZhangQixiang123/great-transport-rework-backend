"""
Training pipeline for competitor video scoring model.

Primary mode: regression on log(views) with pre-upload + YouTube features.
"""
from .features import (
    extract_features_single,
    extract_features_dataframe,
    extract_labels,
    extract_regression_target,
    load_regression_data,
    load_training_data,
    FEATURE_NAMES,
    PRE_UPLOAD_FEATURES,
    YOUTUBE_FEATURES,
)
from .data_validator import validate_training_data, ValidationResult
from .trainer import train_model
from .evaluator import evaluate_model, evaluate_regression, EvaluationReport, RegressionReport

__all__ = [
    "extract_features_single",
    "extract_features_dataframe",
    "extract_labels",
    "extract_regression_target",
    "load_regression_data",
    "load_training_data",
    "FEATURE_NAMES",
    "PRE_UPLOAD_FEATURES",
    "YOUTUBE_FEATURES",
    "validate_training_data",
    "ValidationResult",
    "train_model",
    "evaluate_model",
    "evaluate_regression",
    "EvaluationReport",
    "RegressionReport",
]
