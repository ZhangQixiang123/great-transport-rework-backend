"""Tests for app.description module."""
import pytest
from unittest.mock import MagicMock

from app.description import (
    format_view_count,
    fallback_description,
    generate_description,
    translate_title,
)


# ── format_view_count ─────────────────────────────────────────────────


class TestFormatViewCount:
    def test_billions(self):
        assert format_view_count(1_5000_0000) == "1.5亿次观看"

    def test_exact_billion(self):
        assert format_view_count(1_0000_0000) == "1亿次观看"

    def test_ten_thousands(self):
        assert format_view_count(150_0000) == "150万次观看"

    def test_exact_ten_thousand(self):
        assert format_view_count(1_0000) == "1万次观看"

    def test_fractional_ten_thousand(self):
        assert format_view_count(1_5000) == "1.5万次观看"

    def test_small_number(self):
        assert format_view_count(9999) == "9999次观看"

    def test_zero(self):
        assert format_view_count(0) == "0次观看"

    def test_one(self):
        assert format_view_count(1) == "1次观看"

    def test_large_billion(self):
        assert format_view_count(35_0000_0000) == "35亿次观看"


# ── fallback_description ──────────────────────────────────────────────


class TestFallbackDescription:
    def test_with_title(self):
        info = {"title": "Test Video", "view_count": 1_0000, "video_id": "abc123"}
        desc = fallback_description(info)
        assert "【Test Video】" in desc
        assert "本视频搬运自YouTube" in desc
        assert "1万次观看" in desc
        assert "https://www.youtube.com/watch?v=abc123" in desc

    def test_without_title(self):
        info = {"view_count": 500, "video_id": "xyz"}
        desc = fallback_description(info)
        assert "本视频搬运自YouTube" in desc
        assert "500次观看" in desc

    def test_without_video_id(self):
        info = {"title": "No ID", "view_count": 100}
        desc = fallback_description(info)
        assert "本视频搬运自YouTube" in desc
        assert "youtube.com" not in desc

    def test_large_views(self):
        info = {"title": "Popular", "view_count": 5_0000_0000, "video_id": "big"}
        desc = fallback_description(info)
        assert "5亿次观看" in desc


# ── generate_description ──────────────────────────────────────────────


class TestGenerateDescription:
    def _make_info(self):
        return {
            "title": "Gordon Ramsay Makes Fish and Chips",
            "view_count": 15_000_000,
            "channel": "Gordon Ramsay",
            "video_id": "abc123",
        }

    def test_with_llm(self):
        backend = MagicMock()
        backend.chat.return_value = "厨神戈登秀出炸鱼薯条的终极做法！"
        info = self._make_info()
        desc = generate_description(backend, info)
        assert "厨神戈登秀出炸鱼薯条的终极做法！" in desc
        assert "本视频搬运自YouTube" in desc
        assert "1500万次观看" in desc
        assert "https://www.youtube.com/watch?v=abc123" in desc
        backend.chat.assert_called_once()

    def test_llm_returns_quoted_string(self):
        backend = MagicMock()
        backend.chat.return_value = '"这是一个好视频"'
        info = self._make_info()
        desc = generate_description(backend, info)
        assert desc.startswith("这是一个好视频")
        assert "本视频搬运自YouTube" in desc

    def test_llm_fails_gracefully(self):
        backend = MagicMock()
        backend.chat.side_effect = RuntimeError("connection refused")
        info = self._make_info()
        desc = generate_description(backend, info)
        assert "本视频搬运自YouTube" in desc
        assert "1500万次观看" in desc

    def test_none_backend_uses_fallback(self):
        info = self._make_info()
        desc = generate_description(None, info)
        assert "本视频搬运自YouTube" in desc
        assert "【Gordon Ramsay Makes Fish and Chips】" in desc

    def test_llm_returns_empty(self):
        backend = MagicMock()
        backend.chat.return_value = ""
        info = self._make_info()
        desc = generate_description(backend, info)
        # Should fall back
        assert "【Gordon Ramsay Makes Fish and Chips】" in desc

    def test_llm_returns_too_long(self):
        backend = MagicMock()
        backend.chat.return_value = "字" * 300
        info = self._make_info()
        desc = generate_description(backend, info)
        # Should fall back
        assert "【Gordon Ramsay Makes Fish and Chips】" in desc


# ── upload_client ─────────────────────────────────────────────────────


class TestUploadClient:
    def test_import(self):
        from app.upload_client import UploadClient
        client = UploadClient("http://localhost:9999")
        assert client.go_url == "http://localhost:9999"

    def test_url_trailing_slash(self):
        from app.upload_client import UploadClient
        client = UploadClient("http://localhost:8080/")
        assert client.go_url == "http://localhost:8080"


# ── translate_title ──────────────────────────────────────────────────


class TestTranslateTitle:
    def test_successful_translation(self):
        backend = MagicMock()
        backend.chat.return_value = '{"chinese_title": "Gordon Ramsay做出完美炸鱼薯条"}'
        result = translate_title(backend, "Gordon Ramsay Makes the Perfect Fish and Chips")
        assert result == "Gordon Ramsay做出完美炸鱼薯条"
        backend.chat.assert_called_once()

    def test_none_backend_returns_original(self):
        result = translate_title(None, "Some English Title")
        assert result == "Some English Title"

    def test_empty_title_returns_original(self):
        backend = MagicMock()
        result = translate_title(backend, "")
        assert result == ""
        backend.chat.assert_not_called()

    def test_backend_error_returns_original(self):
        backend = MagicMock()
        backend.chat.side_effect = RuntimeError("connection refused")
        result = translate_title(backend, "Some Title")
        assert result == "Some Title"

    def test_invalid_json_returns_original(self):
        backend = MagicMock()
        backend.chat.return_value = "not valid json at all"
        result = translate_title(backend, "Some Title")
        assert result == "Some Title"

    def test_empty_chinese_title_returns_original(self):
        backend = MagicMock()
        backend.chat.return_value = '{"chinese_title": ""}'
        result = translate_title(backend, "Some Title")
        assert result == "Some Title"

    def test_missing_key_returns_original(self):
        backend = MagicMock()
        backend.chat.return_value = '{"wrong_key": "value"}'
        result = translate_title(backend, "Some Title")
        assert result == "Some Title"

    def test_whitespace_only_returns_original(self):
        backend = MagicMock()
        backend.chat.return_value = '{"chinese_title": "   "}'
        result = translate_title(backend, "Some Title")
        assert result == "Some Title"

    def test_passes_json_schema(self):
        backend = MagicMock()
        backend.chat.return_value = '{"chinese_title": "测试标题"}'
        translate_title(backend, "Test Title")
        call_args = backend.chat.call_args
        assert call_args.kwargs.get("json_schema") or call_args[1].get("json_schema")
        schema = call_args.kwargs.get("json_schema") or call_args[1].get("json_schema")
        assert schema["required"] == ["chinese_title"]
