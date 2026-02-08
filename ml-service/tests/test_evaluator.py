"""Tests for model evaluation."""
import json

import lightgbm as lgb
import numpy as np
import pytest

from app.training.evaluator import RegressionReport, evaluate_regression
from app.training.features import FEATURE_NAMES


def _train_tiny_regression_model():
    """Train a small regression model for testing evaluator."""
    rng = np.random.RandomState(42)
    n = 100
    n_features = len(FEATURE_NAMES)
    X = rng.randn(n, n_features)
    # Target: log_views with signal from first feature
    y = 8.0 + X[:, 0] * 2 + rng.randn(n) * 0.5

    train_data = lgb.Dataset(X, label=y, feature_name=FEATURE_NAMES)
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
        report = evaluate_regression(model, X, y, FEATURE_NAMES)

        assert isinstance(report, RegressionReport)
        assert report.rmse > 0
        assert report.mae > 0
        assert report.median_ae > 0
        assert -10.0 <= report.r2 <= 1.0
        assert -1.0 <= report.correlation <= 1.0

    def test_accuracy_bounds(self):
        """Within-X-log accuracy is between 0 and 1."""
        model, X, y = _train_tiny_regression_model()
        report = evaluate_regression(model, X, y, FEATURE_NAMES)

        assert 0.0 <= report.within_1_log <= 1.0
        assert 0.0 <= report.within_2_log <= 1.0
        assert report.within_2_log >= report.within_1_log

    def test_feature_importance(self):
        """Feature importance has all features."""
        model, X, y = _train_tiny_regression_model()
        report = evaluate_regression(model, X, y, FEATURE_NAMES)

        assert len(report.feature_importance) == len(FEATURE_NAMES)
        for fname in FEATURE_NAMES:
            assert fname in report.feature_importance

    def test_to_dict_json_serializable(self):
        """to_dict output is JSON-serializable."""
        model, X, y = _train_tiny_regression_model()
        report = evaluate_regression(model, X, y, FEATURE_NAMES)

        d = report.to_dict()
        json_str = json.dumps(d)
        assert isinstance(json_str, str)
        parsed = json.loads(json_str)
        assert parsed["rmse"] == d["rmse"]

    def test_to_json(self):
        """to_json produces valid JSON."""
        model, X, y = _train_tiny_regression_model()
        report = evaluate_regression(model, X, y, FEATURE_NAMES)

        json_str = report.to_json()
        parsed = json.loads(json_str)
        assert "rmse" in parsed
        assert "r2" in parsed

    def test_summary_string(self):
        """Summary produces readable text."""
        model, X, y = _train_tiny_regression_model()
        report = evaluate_regression(model, X, y, FEATURE_NAMES)

        summary = report.summary()
        assert "RMSE:" in summary
        assert "R2:" in summary
        assert "Top 10 features" in summary

    def test_test_samples_count(self):
        """Test samples count is correct."""
        model, X, y = _train_tiny_regression_model()
        report = evaluate_regression(model, X, y, FEATURE_NAMES)
        assert report.test_samples == len(y)
