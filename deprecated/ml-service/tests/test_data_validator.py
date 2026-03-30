"""Tests for data validation."""
import numpy as np
import pytest

from app.training.data_validator import (
    MIN_CLASSES,
    MIN_SAMPLES_PER_CLASS,
    MIN_TOTAL_SAMPLES,
    WARN_SAMPLES_PER_CLASS,
    WARN_TOTAL_SAMPLES,
    ValidationResult,
    validate_training_data,
)


class TestValidateTrainingData:
    def test_valid_data(self):
        """Enough data across all classes passes validation."""
        # 200 samples: 50 per class
        labels = np.array([0]*50 + [1]*50 + [2]*50 + [3]*50)
        result = validate_training_data(labels)
        assert result.is_valid is True
        assert result.total_samples == 200
        assert len(result.errors) == 0
        assert len(result.warnings) == 0

    def test_empty_data(self):
        """No data fails validation."""
        labels = np.array([], dtype=np.int32)
        result = validate_training_data(labels)
        assert result.is_valid is False
        assert result.total_samples == 0
        assert len(result.errors) >= 1

    def test_too_few_total_samples(self):
        """Below minimum total threshold."""
        labels = np.array([0]*10 + [1]*10)
        result = validate_training_data(labels)
        assert result.is_valid is False
        assert any("at least" in e and "labeled samples" in e for e in result.errors)

    def test_too_few_per_class(self):
        """One class has fewer than MIN_SAMPLES_PER_CLASS."""
        # 50 total, but class 2 has only 2 samples
        labels = np.array([0]*24 + [1]*24 + [2]*2)
        result = validate_training_data(labels)
        assert result.is_valid is False
        assert any("successful" in e for e in result.errors)

    def test_single_class(self):
        """Only one class present fails the min classes check."""
        labels = np.array([0]*100)
        result = validate_training_data(labels)
        assert result.is_valid is False
        assert any("different label classes" in e for e in result.errors)

    def test_two_classes_is_enough(self):
        """Two classes meets minimum class requirement."""
        labels = np.array([0]*30 + [1]*30)
        result = validate_training_data(labels)
        assert result.is_valid is True

    def test_warnings_low_total(self):
        """Valid but below recommended total triggers warning."""
        labels = np.array([0]*30 + [1]*30)
        result = validate_training_data(labels)
        assert result.is_valid is True
        assert any("samples" in w and "underfit" in w for w in result.warnings)

    def test_warnings_low_per_class(self):
        """Valid but a class below recommended count triggers warning."""
        # 4 classes, class 3 has only 10 (above min 5, below warn 20)
        labels = np.array([0]*60 + [1]*60 + [2]*60 + [3]*10)
        result = validate_training_data(labels)
        assert result.is_valid is True
        assert any("viral" in w for w in result.warnings)

    def test_custom_min_samples(self):
        """Custom min_samples override works."""
        labels = np.array([0]*10 + [1]*10)
        result = validate_training_data(labels, min_samples=15)
        assert result.is_valid is True

    def test_class_distribution_correct(self):
        """Class distribution reports correct counts."""
        labels = np.array([0]*10 + [1]*20 + [2]*30 + [3]*40)
        result = validate_training_data(labels)
        assert result.class_distribution["failed"] == 10
        assert result.class_distribution["standard"] == 20
        assert result.class_distribution["successful"] == 30
        assert result.class_distribution["viral"] == 40

    def test_summary_includes_info(self):
        """Summary string is well-formed."""
        labels = np.array([0]*5 + [1]*5)
        result = validate_training_data(labels)
        summary = result.summary()
        assert "Samples: 10" in summary
