"""Tests for the finetuning package: data preparation, LoRA config, export."""
import json
import math
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.finetuning.prepare_data import (
    ASSISTANT_TEMPLATE,
    SYSTEM_MESSAGE,
    USER_TEMPLATE,
    _generate_reasoning,
    _label_from_log_views,
    prepare_training_data,
)
from app.finetuning.train_lora import (
    DEFAULT_BASE_MODEL,
    DEFAULT_LORA_CONFIG,
    DEFAULT_TRAINING_ARGS,
)
from app.finetuning.export_model import (
    MODELFILE_TEMPLATE,
    create_ollama_model,
    merge_lora,
)


# ── Label Classification ────────────────────────────────────────────


class TestLabelFromLogViews:
    def test_failed(self):
        assert _label_from_log_views(5.0) == "failed"
        assert _label_from_log_views(7.5) == "failed"

    def test_standard(self):
        assert _label_from_log_views(7.6) == "standard"
        assert _label_from_log_views(9.0) == "standard"

    def test_successful(self):
        assert _label_from_log_views(10.3) == "successful"
        assert _label_from_log_views(11.0) == "successful"

    def test_viral(self):
        assert _label_from_log_views(12.2) == "viral"
        assert _label_from_log_views(15.0) == "viral"

    def test_boundary_values(self):
        # Exactly at boundaries
        assert _label_from_log_views(7.6) == "standard"  # >= p25
        assert _label_from_log_views(10.3) == "successful"  # >= p75
        assert _label_from_log_views(12.2) == "viral"  # >= p95

    def test_custom_percentiles(self):
        assert _label_from_log_views(5.0, p25=6.0, p75=9.0, p95=11.0) == "failed"
        assert _label_from_log_views(7.0, p25=6.0, p75=9.0, p95=11.0) == "standard"
        assert _label_from_log_views(10.0, p25=6.0, p75=9.0, p95=11.0) == "successful"
        assert _label_from_log_views(12.0, p25=6.0, p75=9.0, p95=11.0) == "viral"


# ── Reasoning Generation ────────────────────────────────────────────


class TestGenerateReasoning:
    def _mock_video(self):
        v = MagicMock()
        v.title = "Test Video"
        v.duration = 300
        return v

    def test_viral_reasoning(self):
        r = _generate_reasoning(self._mock_video(), 13.0, "viral")
        assert "exceptional" in r.lower() or "views" in r.lower()

    def test_successful_reasoning(self):
        r = _generate_reasoning(self._mock_video(), 10.5, "successful")
        assert "solid" in r.lower() or "performance" in r.lower()

    def test_standard_reasoning(self):
        r = _generate_reasoning(self._mock_video(), 8.5, "standard")
        assert "average" in r.lower() or "moderate" in r.lower()

    def test_failed_reasoning(self):
        r = _generate_reasoning(self._mock_video(), 6.0, "failed")
        assert "below" in r.lower() or "lack" in r.lower()


# ── Templates ────────────────────────────────────────────────────────


class TestTemplates:
    def test_system_message_not_empty(self):
        assert len(SYSTEM_MESSAGE) > 50
        assert "bilibili" in SYSTEM_MESSAGE.lower()

    def test_user_template_has_placeholders(self):
        assert "{title}" in USER_TEMPLATE
        assert "{duration}" in USER_TEMPLATE
        assert "{yt_views" in USER_TEMPLATE
        assert "{similar_text}" in USER_TEMPLATE

    def test_user_template_formats(self):
        msg = USER_TEMPLATE.format(
            title="Test", duration=300, yt_views=1000,
            yt_likes=50, category_id=22, similar_text="None",
        )
        assert "Test" in msg
        assert "300" in msg

    def test_assistant_template_formats(self):
        msg = ASSISTANT_TEMPLATE.format(
            log_views=10.5, views=36000, confidence=0.8,
            label="successful", reasoning="Good content",
        )
        parsed = json.loads(msg)
        assert parsed["predicted_log_views"] == 10.5
        assert parsed["label"] == "successful"

    def test_modelfile_template_has_gguf_placeholder(self):
        assert "{gguf_path}" in MODELFILE_TEMPLATE
        rendered = MODELFILE_TEMPLATE.format(gguf_path="/path/to/model.gguf")
        assert "/path/to/model.gguf" in rendered


# ── Data Preparation ────────────────────────────────────────────────


class TestPrepareTrainingData:
    def _mock_video(self, bvid, title="Test", uid="ch1", duration=300):
        v = MagicMock()
        v.bvid = bvid
        v.title = title
        v.bilibili_uid = uid
        v.duration = duration
        return v

    def test_too_few_videos_raises(self):
        """Should raise if fewer than 100 videos."""
        db = MagicMock()
        with patch(
            "app.finetuning.prepare_data.load_regression_data",
            return_value=(
                [self._mock_video(f"BV{i}") for i in range(50)],
                [8.0] * 50,
                {},
            ),
        ):
            with pytest.raises(ValueError, match="at least 100"):
                prepare_training_data(db, model_dir=tempfile.mkdtemp())

    def test_basic_output(self):
        """Should create train/val JSONL files with correct structure."""
        n = 200
        videos = [self._mock_video(f"BV{i}", title=f"Video {i}") for i in range(n)]
        targets = [6.0 + (i % 10) for i in range(n)]  # range of labels

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "app.finetuning.prepare_data.load_regression_data",
                return_value=(videos, targets, {}),
            ):
                train_path, val_path, stats = prepare_training_data(
                    MagicMock(), model_dir=tmpdir, train_ratio=0.8,
                )

            assert os.path.exists(train_path)
            assert os.path.exists(val_path)

            # Check stats
            assert stats["total_videos"] == n
            assert stats["train_examples"] == 160
            assert stats["val_examples"] == 40
            assert "label_distribution" in stats

            # Check file contents
            with open(train_path, encoding="utf-8") as f:
                lines = f.readlines()
            assert len(lines) == 160

            example = json.loads(lines[0])
            assert "messages" in example
            assert len(example["messages"]) == 3
            assert example["messages"][0]["role"] == "system"
            assert example["messages"][1]["role"] == "user"
            assert example["messages"][2]["role"] == "assistant"

            # Assistant message should be valid JSON
            assistant_json = json.loads(example["messages"][2]["content"])
            assert "predicted_log_views" in assistant_json
            assert "label" in assistant_json

    def test_train_ratio(self):
        """Custom train ratio should split correctly."""
        n = 200
        videos = [self._mock_video(f"BV{i}") for i in range(n)]
        targets = [8.0] * n

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "app.finetuning.prepare_data.load_regression_data",
                return_value=(videos, targets, {}),
            ):
                _, _, stats = prepare_training_data(
                    MagicMock(), model_dir=tmpdir, train_ratio=0.7,
                )

            assert stats["train_examples"] == 140
            assert stats["val_examples"] == 60


# ── LoRA Config Defaults ────────────────────────────────────────────


class TestLoRAConfig:
    def test_default_base_model(self):
        assert "Qwen" in DEFAULT_BASE_MODEL

    def test_lora_config_keys(self):
        assert "r" in DEFAULT_LORA_CONFIG
        assert "lora_alpha" in DEFAULT_LORA_CONFIG
        assert "target_modules" in DEFAULT_LORA_CONFIG
        assert "task_type" in DEFAULT_LORA_CONFIG

    def test_lora_rank_and_alpha(self):
        assert DEFAULT_LORA_CONFIG["r"] == 16
        assert DEFAULT_LORA_CONFIG["lora_alpha"] == 32

    def test_training_args_keys(self):
        assert "num_train_epochs" in DEFAULT_TRAINING_ARGS
        assert "learning_rate" in DEFAULT_TRAINING_ARGS
        assert "gradient_checkpointing" in DEFAULT_TRAINING_ARGS
        assert DEFAULT_TRAINING_ARGS["bf16"] is True

    def test_target_modules_include_attention(self):
        modules = DEFAULT_LORA_CONFIG["target_modules"]
        assert "q_proj" in modules
        assert "k_proj" in modules
        assert "v_proj" in modules


# ── Export Functions ─────────────────────────────────────────────────


class TestExportFunctions:
    def test_merge_lora_import_error(self):
        """merge_lora should raise ImportError if peft not available."""
        with patch.dict("sys.modules", {"peft": None}):
            with pytest.raises((ImportError, ModuleNotFoundError)):
                merge_lora("base", "adapter", "output")

    def test_create_ollama_model_not_found(self):
        """create_ollama_model should return False if ollama not installed."""
        with patch("subprocess.run", side_effect=FileNotFoundError("ollama")):
            with tempfile.NamedTemporaryFile(suffix=".gguf") as f:
                result = create_ollama_model(f.name, "test-model")
        assert result is False

    def test_create_ollama_model_success(self):
        """create_ollama_model should return True on success."""
        mock_result = MagicMock()
        with patch("subprocess.run", return_value=mock_result):
            with tempfile.NamedTemporaryFile(suffix=".gguf") as f:
                result = create_ollama_model(f.name, "test-model")
        assert result is True

    def test_create_ollama_model_failure(self):
        """create_ollama_model should return False on CalledProcessError."""
        import subprocess
        with patch(
            "subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "ollama", stderr="error"),
        ):
            with tempfile.NamedTemporaryFile(suffix=".gguf") as f:
                result = create_ollama_model(f.name, "test-model")
        assert result is False
