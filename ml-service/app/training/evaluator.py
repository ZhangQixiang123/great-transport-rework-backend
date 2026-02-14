"""
Model evaluation and metrics reporting.

Supports both regression and classification evaluation:
  - Regression: RMSE, MAE, R², correlation, feature importance
  - Classification (legacy): accuracy, F1, AUC, confusion matrix
"""
import json
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
from sklearn.metrics import (
    mean_squared_error,
    mean_absolute_error,
    r2_score,
)

from .features import LABEL_MAP, LABEL_NAMES


@dataclass
class RegressionReport:
    """Evaluation report for a regression model."""
    rmse: float
    mae: float
    r2: float
    correlation: float
    median_ae: float
    feature_importance: Dict[str, float]
    # Percentile accuracy: % of predictions within X of actual
    within_1_log: float  # within 1 log unit (~2.7x)
    within_2_log: float  # within 2 log units (~7.4x)
    test_samples: int
    target_mean: float
    target_std: float

    def summary(self) -> str:
        lines = [
            f"RMSE:          {self.rmse:.4f} (log scale)",
            f"MAE:           {self.mae:.4f} (log scale)",
            f"Median AE:     {self.median_ae:.4f} (log scale)",
            f"R2:            {self.r2:.4f}",
            f"Correlation:   {self.correlation:.4f}",
            f"Within 1 log:  {self.within_1_log:.1%} (predictions within ~2.7x of actual)",
            f"Within 2 log:  {self.within_2_log:.1%} (predictions within ~7.4x of actual)",
            f"Test samples:  {self.test_samples}",
            "",
            "Top 10 features (gain):",
        ]
        sorted_feats = sorted(self.feature_importance.items(), key=lambda x: x[1], reverse=True)
        for feat, gain in sorted_feats[:10]:
            lines.append(f"  {feat:25s} {gain:.1f}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "rmse": self.rmse,
            "mae": self.mae,
            "median_ae": self.median_ae,
            "r2": self.r2,
            "correlation": self.correlation,
            "within_1_log": self.within_1_log,
            "within_2_log": self.within_2_log,
            "test_samples": self.test_samples,
            "target_mean": self.target_mean,
            "target_std": self.target_std,
            "feature_importance": self.feature_importance,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


# Keep legacy class for backward compatibility
@dataclass
class EvaluationReport:
    """Complete evaluation report for a classification model (legacy)."""
    accuracy: float
    weighted_f1: float
    macro_f1: float
    logloss: float
    per_class: Dict[str, Dict[str, float]]
    auc_per_class: Dict[str, float]
    confusion: List[List[int]]
    feature_importance: Dict[str, float]
    class_names: List[str] = field(default_factory=lambda: ["failed", "standard", "successful", "viral"])

    def summary(self) -> str:
        lines = [
            f"Accuracy:    {self.accuracy:.4f}",
            f"Weighted F1: {self.weighted_f1:.4f}",
            f"Macro F1:    {self.macro_f1:.4f}",
            f"Log Loss:    {self.logloss:.4f}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "accuracy": self.accuracy,
            "weighted_f1": self.weighted_f1,
            "macro_f1": self.macro_f1,
            "logloss": self.logloss,
            "per_class": self.per_class,
            "auc_per_class": self.auc_per_class,
            "confusion_matrix": self.confusion,
            "feature_importance": self.feature_importance,
            "class_names": self.class_names,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


def evaluate_regression_simple(
    y_pred: np.ndarray,
    y_test: np.ndarray,
) -> Dict[str, float]:
    """Lightweight regression evaluation without feature importance.

    Used for cross-validation folds where we don't need feature importance.

    Args:
        y_pred: Predicted values.
        y_test: True target values.

    Returns:
        Dict with rmse, mae, r2, correlation, within_1_log, within_2_log.
    """
    rmse = float(np.sqrt(mean_squared_error(y_test, y_pred)))
    mae = float(mean_absolute_error(y_test, y_pred))
    r2 = float(r2_score(y_test, y_pred))

    if np.std(y_test) > 0 and np.std(y_pred) > 0:
        correlation = float(np.corrcoef(y_test, y_pred)[0, 1])
    else:
        correlation = 0.0

    abs_errors = np.abs(y_test - y_pred)
    within_1 = float(np.mean(abs_errors <= 1.0))
    within_2 = float(np.mean(abs_errors <= 2.0))

    return {
        "rmse": rmse,
        "mae": mae,
        "r2": r2,
        "correlation": correlation,
        "within_1_log": within_1,
        "within_2_log": within_2,
    }


def evaluate_regression(
    model,
    X_test: np.ndarray,
    y_test: np.ndarray,
    feature_names: List[str],
) -> RegressionReport:
    """Evaluate a trained LightGBM regression model on test data.

    Args:
        model: Trained lgb.Booster instance (regression).
        X_test: Test feature matrix.
        y_test: True targets (log_views).
        feature_names: List of feature column names.

    Returns:
        RegressionReport with all metrics.
    """
    y_pred = model.predict(X_test)

    rmse = float(np.sqrt(mean_squared_error(y_test, y_pred)))
    mae = float(mean_absolute_error(y_test, y_pred))
    r2 = float(r2_score(y_test, y_pred))

    # Correlation
    if np.std(y_test) > 0 and np.std(y_pred) > 0:
        correlation = float(np.corrcoef(y_test, y_pred)[0, 1])
    else:
        correlation = 0.0

    # Median absolute error
    abs_errors = np.abs(y_test - y_pred)
    median_ae = float(np.median(abs_errors))

    # Within-X-log accuracy
    within_1 = float(np.mean(abs_errors <= 1.0))
    within_2 = float(np.mean(abs_errors <= 2.0))

    # Feature importance (gain)
    importance = model.feature_importance(importance_type="gain")
    feat_imp: Dict[str, float] = {}
    for fname, imp in zip(feature_names, importance):
        feat_imp[fname] = float(imp)

    return RegressionReport(
        rmse=rmse,
        mae=mae,
        r2=r2,
        correlation=correlation,
        median_ae=median_ae,
        feature_importance=feat_imp,
        within_1_log=within_1,
        within_2_log=within_2,
        test_samples=len(y_test),
        target_mean=float(np.mean(y_test)),
        target_std=float(np.std(y_test)),
    )


def evaluate_regression_gpb(
    model,
    X_test: np.ndarray,
    y_test: np.ndarray,
    feature_names: List[str],
    group_data: np.ndarray,
) -> RegressionReport:
    """Evaluate a trained GPBoost model on test data.

    Args:
        model: Trained gpb.Booster instance.
        X_test: Test feature matrix.
        y_test: True targets (log_views).
        feature_names: List of feature column names.
        group_data: Group array for random effects.

    Returns:
        RegressionReport with all metrics.
    """
    pred_dict = model.predict(data=X_test, group_data_pred=group_data)
    y_pred = np.array(pred_dict["response_mean"])

    rmse = float(np.sqrt(mean_squared_error(y_test, y_pred)))
    mae = float(mean_absolute_error(y_test, y_pred))
    r2 = float(r2_score(y_test, y_pred))

    if np.std(y_test) > 0 and np.std(y_pred) > 0:
        correlation = float(np.corrcoef(y_test, y_pred)[0, 1])
    else:
        correlation = 0.0

    abs_errors = np.abs(y_test - y_pred)
    median_ae = float(np.median(abs_errors))
    within_1 = float(np.mean(abs_errors <= 1.0))
    within_2 = float(np.mean(abs_errors <= 2.0))

    importance = model.feature_importance(importance_type="gain")
    feat_imp: Dict[str, float] = {}
    for fname, imp in zip(feature_names, importance):
        feat_imp[fname] = float(imp)

    return RegressionReport(
        rmse=rmse,
        mae=mae,
        r2=r2,
        correlation=correlation,
        median_ae=median_ae,
        feature_importance=feat_imp,
        within_1_log=within_1,
        within_2_log=within_2,
        test_samples=len(y_test),
        target_mean=float(np.mean(y_test)),
        target_std=float(np.std(y_test)),
    )


def evaluate_model(
    model,
    X_test: np.ndarray,
    y_test: np.ndarray,
    feature_names: List[str],
    num_classes: int = 4,
) -> EvaluationReport:
    """Evaluate a trained LightGBM classifier on test data (legacy).

    Args:
        model: Trained lgb.Booster instance (multiclass).
        X_test: Test feature matrix.
        y_test: True labels (integer encoded).
        feature_names: List of feature column names.
        num_classes: Number of classes (default 4).

    Returns:
        EvaluationReport with all metrics.
    """
    from sklearn.metrics import (
        accuracy_score,
        classification_report,
        confusion_matrix,
        f1_score,
        log_loss,
        roc_auc_score,
    )
    from sklearn.preprocessing import label_binarize

    y_pred_proba = model.predict(X_test)
    y_pred = np.argmax(y_pred_proba, axis=1)

    accuracy = float(accuracy_score(y_test, y_pred))
    weighted_f1 = float(f1_score(y_test, y_pred, average="weighted", zero_division=0))
    macro_f1 = float(f1_score(y_test, y_pred, average="macro", zero_division=0))

    y_pred_proba_clipped = np.clip(y_pred_proba, 1e-15, 1 - 1e-15)
    logloss_val = float(log_loss(y_test, y_pred_proba_clipped, labels=list(range(num_classes))))

    report = classification_report(
        y_test, y_pred,
        labels=list(range(num_classes)),
        target_names=[LABEL_NAMES[i] for i in range(num_classes)],
        output_dict=True,
        zero_division=0,
    )
    per_class: Dict[str, Dict[str, float]] = {}
    for i in range(num_classes):
        name = LABEL_NAMES[i]
        if name in report:
            per_class[name] = {
                "precision": float(report[name]["precision"]),
                "recall": float(report[name]["recall"]),
                "f1": float(report[name]["f1-score"]),
                "support": int(report[name]["support"]),
            }

    auc_per_class: Dict[str, float] = {}
    y_test_bin = label_binarize(y_test, classes=list(range(num_classes)))
    for i in range(num_classes):
        name = LABEL_NAMES[i]
        if y_test_bin[:, i].sum() > 0 and y_test_bin[:, i].sum() < len(y_test):
            try:
                auc_per_class[name] = float(roc_auc_score(y_test_bin[:, i], y_pred_proba[:, i]))
            except ValueError:
                auc_per_class[name] = 0.0
        else:
            auc_per_class[name] = 0.0

    cm = confusion_matrix(y_test, y_pred, labels=list(range(num_classes)))

    importance = model.feature_importance(importance_type="gain")
    feat_imp: Dict[str, float] = {}
    for fname, imp in zip(feature_names, importance):
        feat_imp[fname] = float(imp)

    return EvaluationReport(
        accuracy=accuracy,
        weighted_f1=weighted_f1,
        macro_f1=macro_f1,
        logloss=logloss_val,
        per_class=per_class,
        auc_per_class=auc_per_class,
        confusion=cm.tolist(),
        feature_importance=feat_imp,
    )
