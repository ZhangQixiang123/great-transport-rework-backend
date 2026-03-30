"""
Data validation for training pipeline.

Checks minimum data requirements before training begins, providing
clear error messages when the dataset is insufficient.
"""
from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np

from .features import LABEL_NAMES

MIN_TOTAL_SAMPLES = 50
MIN_SAMPLES_PER_CLASS = 5
MIN_CLASSES = 2
WARN_TOTAL_SAMPLES = 200
WARN_SAMPLES_PER_CLASS = 20


@dataclass
class ValidationResult:
    """Result of training data validation."""
    is_valid: bool
    total_samples: int
    class_distribution: Dict[str, int]
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"Samples: {self.total_samples}"]
        for name, count in sorted(self.class_distribution.items()):
            lines.append(f"  {name}: {count}")
        if self.errors:
            lines.append("Errors:")
            for e in self.errors:
                lines.append(f"  - {e}")
        if self.warnings:
            lines.append("Warnings:")
            for w in self.warnings:
                lines.append(f"  - {w}")
        return "\n".join(lines)


def validate_training_data(labels: np.ndarray, min_samples: int = MIN_TOTAL_SAMPLES) -> ValidationResult:
    """Validate that training data meets minimum requirements.

    Args:
        labels: Array of integer class labels.
        min_samples: Override minimum total samples (default 50).

    Returns:
        ValidationResult with is_valid flag, distribution, warnings, and errors.
    """
    errors: List[str] = []
    warnings: List[str] = []
    total = len(labels)

    # Build class distribution
    class_dist: Dict[str, int] = {}
    if total > 0:
        unique, counts = np.unique(labels, return_counts=True)
        for cls_id, count in zip(unique, counts):
            name = LABEL_NAMES.get(int(cls_id), f"unknown_{cls_id}")
            class_dist[name] = int(count)

    num_classes = len(class_dist)

    # Error checks
    if total < min_samples:
        errors.append(
            f"Need at least {min_samples} labeled samples, got {total}. "
            f"Run 'label-videos' to label more competitor videos."
        )

    if num_classes < MIN_CLASSES:
        errors.append(
            f"Need at least {MIN_CLASSES} different label classes, got {num_classes}. "
            f"Collect more diverse competitor videos."
        )

    for name, count in class_dist.items():
        if count < MIN_SAMPLES_PER_CLASS:
            errors.append(
                f"Class '{name}' has only {count} samples (minimum {MIN_SAMPLES_PER_CLASS}). "
                f"Collect more '{name}' examples."
            )

    # Warning checks (only if no errors for these)
    if total < WARN_TOTAL_SAMPLES and total >= min_samples:
        warnings.append(
            f"Only {total} samples — model may underfit. "
            f"Recommend at least {WARN_TOTAL_SAMPLES} for reliable results."
        )

    for name, count in class_dist.items():
        if count < WARN_SAMPLES_PER_CLASS and count >= MIN_SAMPLES_PER_CLASS:
            warnings.append(
                f"Class '{name}' has only {count} samples — recommend {WARN_SAMPLES_PER_CLASS}+."
            )

    is_valid = len(errors) == 0
    return ValidationResult(
        is_valid=is_valid,
        total_samples=total,
        class_distribution=class_dist,
        warnings=warnings,
        errors=errors,
    )
