"""Tests for the LLM backend abstraction and LLM predictor."""
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.llm.backend import CloudBackend, LLMBackend, OllamaBackend, create_backend
from app.prediction.llm_predictor import LLMPredictor
from app.prediction.models import VideoPredictionResult


# ── LLM Backend Tests ────────────────────────────────────────────────


class TestCreateBackend:
    def test_create_ollama(self):
        with patch("app.llm.backend.OllamaBackend._verify_connection"):
            backend = create_backend("ollama", model="test:latest")
        assert isinstance(backend, OllamaBackend)

    def test_create_openai(self):
        with patch("app.llm.backend.CloudBackend._init_openai"):
            backend = create_backend("openai", model="gpt-4o-mini")
        assert isinstance(backend, CloudBackend)
        assert backend.provider == "openai"

    def test_create_anthropic(self):
        with patch("app.llm.backend.CloudBackend._init_anthropic"):
            backend = create_backend("anthropic")
        assert isinstance(backend, CloudBackend)
        assert backend.provider == "anthropic"

    def test_create_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown backend type"):
            create_backend("unknown_backend")

    def test_case_insensitive(self):
        with patch("app.llm.backend.OllamaBackend._verify_connection"):
            backend = create_backend("OLLAMA")
        assert isinstance(backend, OllamaBackend)


class TestOllamaBackend:
    def test_chat(self):
        with patch("app.llm.backend.OllamaBackend._verify_connection"):
            backend = OllamaBackend(model="test:latest")

        mock_response = MagicMock()
        mock_response.message.content = '{"key": "value"}'

        mock_ollama = MagicMock()
        mock_ollama.chat.return_value = mock_response

        with patch.dict("sys.modules", {"ollama": mock_ollama}):
            result = backend.chat(
                messages=[{"role": "user", "content": "test"}],
                json_schema={"type": "object"},
            )

        assert result == '{"key": "value"}'
        mock_ollama.chat.assert_called_once()


class TestCloudBackend:
    def test_openai_chat(self):
        with patch("app.llm.backend.CloudBackend._init_openai"):
            backend = CloudBackend(provider="openai", model="gpt-4o-mini")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"result": 42}'
        backend._client = MagicMock()
        backend._client.chat.completions.create.return_value = mock_response

        result = backend.chat(
            messages=[{"role": "user", "content": "test"}],
            json_schema={"type": "object"},
        )
        assert result == '{"result": 42}'

    def test_anthropic_chat(self):
        with patch("app.llm.backend.CloudBackend._init_anthropic"):
            backend = CloudBackend(provider="anthropic")

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"result": 42}')]
        backend._client = MagicMock()
        backend._client.messages.create.return_value = mock_response

        result = backend.chat(
            messages=[
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "test"},
            ],
        )
        assert result == '{"result": 42}'
        # Check system was extracted
        call_kwargs = backend._client.messages.create.call_args[1]
        assert "system" in call_kwargs


class TestLLMBackendProtocol:
    def test_protocol_check(self):
        """Both OllamaBackend and CloudBackend satisfy LLMBackend protocol."""
        with patch("app.llm.backend.OllamaBackend._verify_connection"):
            ollama = OllamaBackend()
        assert isinstance(ollama, LLMBackend)

        with patch("app.llm.backend.CloudBackend._init_openai"):
            cloud = CloudBackend(provider="openai")
        assert isinstance(cloud, LLMBackend)


# ── VideoPredictionResult ──────────────────────────────────────────


class TestVideoPredictionResult:
    def test_label_from_log_views(self):
        assert VideoPredictionResult.label_from_log_views(6.0) == "failed"
        assert VideoPredictionResult.label_from_log_views(8.0) == "standard"
        assert VideoPredictionResult.label_from_log_views(11.0) == "successful"
        assert VideoPredictionResult.label_from_log_views(13.0) == "viral"


# ── LLM Predictor Tests ─────────────────────────────────────────────


class TestLLMPredictor:
    def _make_mock_backend(self, response_dict):
        backend = MagicMock()
        backend.chat.return_value = json.dumps(response_dict)
        return backend

    def test_predict_success(self):
        response = {
            "predicted_log_views": 10.0,
            "predicted_views": 22025,
            "confidence": 0.8,
            "label": "successful",
            "reasoning": "Good content for Bilibili",
        }
        backend = self._make_mock_backend(response)
        predictor = LLMPredictor(backend=backend)

        result = predictor.predict(
            title="Amazing Science Video",
            channel="SciChannel",
            yt_views=500000,
            yt_likes=25000,
            yt_comments=3000,
            duration_seconds=600,
            category_id=28,
        )

        assert result is not None
        assert result["predicted_log_views"] == 10.0
        assert result["confidence"] == 0.8
        assert result["label"] == "successful"
        assert result["reasoning"] == "Good content for Bilibili"
        backend.chat.assert_called_once()

    def test_predict_with_nn_prediction(self):
        response = {
            "predicted_log_views": 11.0,
            "predicted_views": 59873,
            "confidence": 0.9,
            "label": "successful",
            "reasoning": "Neural model agrees with strong similar video data",
        }
        backend = self._make_mock_backend(response)
        predictor = LLMPredictor(backend=backend)

        result = predictor.predict(
            title="Test Video",
            channel="TestCh",
            yt_views=100000,
            yt_likes=5000,
            yt_comments=500,
            duration_seconds=300,
            category_id=22,
            nn_prediction=10.5,
            vectorstore_examples=[
                {"log_views": 10.2, "similarity": 0.85, "rank": 1, "bvid": "BV123"},
            ],
            novelty_info={"novelty_score": 0.8, "similar_count": 2},
        )

        assert result is not None
        assert result["predicted_log_views"] == 11.0
        # Check prompt includes NN prediction context
        call_args = backend.chat.call_args
        messages = call_args[1]["messages"] if "messages" in call_args[1] else call_args[0][0]
        prompt_text = messages[-1]["content"]
        assert "neural" in prompt_text.lower()

    def test_predict_clamps_values(self):
        response = {
            "predicted_log_views": 20.0,  # too high
            "predicted_views": 999999999,
            "confidence": 1.5,  # over 1.0
            "label": "viral",
            "reasoning": "test",
        }
        backend = self._make_mock_backend(response)
        predictor = LLMPredictor(backend=backend)

        result = predictor.predict(
            title="Test", channel="Ch", yt_views=1000, yt_likes=100,
            yt_comments=10, duration_seconds=300, category_id=22,
        )

        assert result["predicted_log_views"] == 16.0  # clamped
        assert result["confidence"] == 1.0  # clamped

    def test_predict_error_returns_none(self):
        backend = MagicMock()
        backend.chat.side_effect = Exception("LLM offline")
        predictor = LLMPredictor(backend=backend)

        result = predictor.predict(
            title="Test", channel="Ch", yt_views=1000, yt_likes=100,
            yt_comments=10, duration_seconds=300, category_id=22,
        )

        assert result is None

    def test_predict_invalid_json_returns_none(self):
        backend = MagicMock()
        backend.chat.return_value = "not valid json {"
        predictor = LLMPredictor(backend=backend)

        result = predictor.predict(
            title="Test", channel="Ch", yt_views=1000, yt_likes=100,
            yt_comments=10, duration_seconds=300, category_id=22,
        )

        assert result is None
