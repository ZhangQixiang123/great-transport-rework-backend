"""Tests for model evaluation."""
import json

import lightgbm as lgb
import numpy as np
import pytest

from app.training.evaluator import RegressionReport, evaluate_regression, evaluate_regression_simple
from app.training.features import FEATURE_NAMES


def _train_tiny_regression_model():
    """Train a small regression model for testing evaluator."""
    rng = np.random.RandomState(42)
    n = 100
    n_features = len(FEATURE_NAMES)
    X = rng.randn(n, n_features)
    # Target: log_views with signal from first feature
    y = 8.0 + X[:, 0] * 2 + rng.randn(n) * 0.5

    train_data = lgb.Dataset(X, label=y, feature_name=list(FEATURE_NAMES))
    params = {
        "objective": "regression",
        "metric": "rmse",
        "num_leaves": 8,
        "learning_rate": 0.1,
        "verbose": -1,
    }
    model = lgb.train(params, train_data, num_boost_round=20)
    return model, X, y


class TestEvaluateRegression:
    def test_report_fields(self):
        """Report contains all expected fields."""
        model, X, y = _train_tiny_regression_model()
        report = evaluate_regression(model, X, y, list(FEATURE_NAMES))

        assert isinstance(report, RegressionReport)
        assert report.rmse > 0
        assert report.mae > 0
        assert report.median_ae > 0
        assert -10.0 <= report.r2 <= 1.0
        assert -1.0 <= report.correlation <= 1.0

    def test_accuracy_bounds(self):
        """Within-X-log accuracy is between 0 and 1."""
        model, X, y = _train_tiny_regression_model()
        report = evaluate_regression(model, X, y, list(FEATURE_NAMES))

        assert 0.0 <= report.within_1_log <= 1.0
        assert 0.0 <= report.within_2_log <= 1.0
        assert report.within_2_log >= report.within_1_log

    def test_feature_importance(self):
        """Feature importance has all features."""
        model, X, y = _train_tiny_regression_model()
        report = evaluate_regression(model, X, y, list(FEATURE_NAMES))

        assert len(report.feature_importance) == len(FEATURE_NAMES)
        for fname in FEATURE_NAMES:
            assert fname in report.feature_importance

    def test_to_dict_json_serializable(self):
        """to_dict output is JSON-serializable."""
        model, X, y = _train_tiny_regression_model()
        report = evaluate_regression(model, X, y, list(FEATURE_NAMES))

        d = report.to_dict()
        json_str = json.dumps(d)
        assert isinstance(json_str, str)
        parsed = json.loads(json_str)
        assert parsed["rmse"] == d["rmse"]

    def test_to_json(self):
        """to_json produces valid JSON."""
        model, X, y = _train_tiny_regression_model()
        report = evaluate_regression(model, X, y, list(FEATURE_NAMES))

        json_str = report.to_json()
        parsed = json.loads(json_str)
        assert "rmse" in parsed
        assert "r2" in parsed

    def test_summary_string(self):
        """Summary produces readable text."""
        model, X, y = _train_tiny_regression_model()
        report = evaluate_regression(model, X, y, list(FEATURE_NAMES))

        summary = report.summary()
        assert "RMSE:" in summary
        assert "R2:" in summary
        assert "Top 10 features" in summary

    def test_test_samples_count(self):
        """Test samples count is correct."""
        model, X, y = _train_tiny_regression_model()
        report = evaluate_regression(model, X, y, list(FEATURE_NAMES))
        assert report.test_samples == len(y)


class TestEvaluateRegressionSimple:
    def test_returns_dict(self):
        """Returns a dict with expected keys."""
        y_true = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y_pred = np.array([1.1, 2.2, 2.8, 4.1, 5.3])
        result = evaluate_regression_simple(y_pred, y_true)

        assert isinstance(result, dict)
        assert "rmse" in result
        assert "mae" in result
        assert "r2" in result
        assert "correlation" in result
        assert "within_1_log" in result
        assert "within_2_log" in result

    def test_perfect_predictions(self):
        """Perfect predictions give R2=1 and RMSE=0."""
        y = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = evaluate_regression_simple(y, y)
        assert result["rmse"] == pytest.approx(0.0)
        assert result["r2"] == pytest.approx(1.0)
        assert result["within_1_log"] == 1.0

    def test_no_feature_importance(self):
        """Simple eval does not include feature importance."""
        y_true = np.array([1.0, 2.0, 3.0])
        y_pred = np.array([1.1, 2.2, 2.8])
        result = evaluate_regression_simple(y_pred, y_true)
        assert "feature_importance" not in result
