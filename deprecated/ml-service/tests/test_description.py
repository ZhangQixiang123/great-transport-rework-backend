"""Tests for app.description module."""
import json
import pytest
from unittest.mock import MagicMock

from app.description import (
    format_view_count,
    fallback_description,
    generate_description,
    generate_persona_copy,
    translate_title,
    STRATEGY_HINTS,
    _PERSONA_EXAMPLES,
    _parse_persona_output,
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


# ── _parse_persona_output ────────────────────────────────────────────


class TestParsePersonaOutput:
    def test_delimiter_format(self):
        text = "标题：200小时之后我可以负责任地说 这游戏就是在浪费你的硬盘\n简介：你预购的时候信誓旦旦说'这次不一样'，200小时之后又开始骂街。"
        title, desc = _parse_persona_output(text)
        assert title == "200小时之后我可以负责任地说 这游戏就是在浪费你的硬盘"
        assert "你预购的时候" in desc

    def test_delimiter_with_half_width_colon(self):
        text = "标题:测试标题\n简介:测试简介内容"
        title, desc = _parse_persona_output(text)
        assert title == "测试标题"
        assert desc == "测试简介内容"

    def test_json_fallback(self):
        text = json.dumps({"title": "JSON标题", "description": "JSON简介"})
        title, desc = _parse_persona_output(text)
        assert title == "JSON标题"
        assert desc == "JSON简介"

    def test_empty_input(self):
        title, desc = _parse_persona_output("")
        assert title == ""
        assert desc == ""

    def test_garbage_input(self):
        title, desc = _parse_persona_output("random garbage text without delimiters")
        assert title == ""
        assert desc == ""

    def test_multiline_description(self):
        text = "标题：测试标题\n简介：第一句话。\n第二句话。\n第三句话。"
        title, desc = _parse_persona_output(text)
        assert title == "测试标题"
        assert "第一句话" in desc
        assert "第三句话" in desc


# ── generate_persona_copy ────────────────────────────────────────────


class TestGeneratePersonaCopy:
    def _make_info(self):
        return {
            "title": "I Beat Every Souls Boss Without Rolling",
            "view_count": 4_200_000,
            "channel": "SomeDude",
            "video_id": "xyz789",
            "duration_seconds": 1200,
            "category_id": 20,
        }

    def test_successful_generation(self):
        backend = MagicMock()
        backend.chat.return_value = (
            "标题：不翻滚通关全魂系 收藏夹里攻略吃灰的你可以散了\n"
            "简介：你收藏了十几个攻略一个没看，人家站桩无伤。油管420万播放，搬过来给你受点刺激。"
        )
        info = self._make_info()
        result = generate_persona_copy(backend, info, strategy_name="gaming_deep_dive")
        assert result["title"] == "不翻滚通关全魂系 收藏夹里攻略吃灰的你可以散了"
        assert "油管420万" in result["description"]
        assert "https://www.youtube.com/watch?v=xyz789" in result["description"]
        backend.chat.assert_called_once()

    def test_none_backend(self):
        info = self._make_info()
        result = generate_persona_copy(None, info)
        assert result["title"] == info["title"]
        assert "本视频搬运自YouTube" in result["description"]

    def test_backend_error_fallback(self):
        backend = MagicMock()
        backend.chat.side_effect = RuntimeError("connection refused")
        info = self._make_info()
        result = generate_persona_copy(backend, info)
        assert result["title"] == info["title"]
        assert "本视频搬运自YouTube" in result["description"]

    def test_invalid_output_fallback(self):
        backend = MagicMock()
        backend.chat.return_value = "not parseable at all"
        info = self._make_info()
        result = generate_persona_copy(backend, info)
        assert result["title"] == info["title"]

    def test_empty_title_fallback(self):
        backend = MagicMock()
        backend.chat.return_value = "标题：\n简介：some desc"
        info = self._make_info()
        result = generate_persona_copy(backend, info)
        assert result["title"] == info["title"]

    def test_title_too_long_fallback(self):
        backend = MagicMock()
        backend.chat.return_value = f"标题：{'字' * 100}\n简介：desc"
        info = self._make_info()
        result = generate_persona_copy(backend, info)
        assert result["title"] == info["title"]

    def test_strategy_hint_in_prompt(self):
        backend = MagicMock()
        backend.chat.return_value = "标题：测试标题\n简介：测试简介"
        info = self._make_info()
        generate_persona_copy(backend, info, strategy_name="surveillance_dashcam")
        call_args = backend.chat.call_args
        messages = call_args.kwargs.get("messages", call_args[0][0] if call_args[0] else [])
        user_msg = messages[-1]["content"]
        assert "surveillance_dashcam" in user_msg
        assert "神人TV" in user_msg

    def test_no_strategy_no_hint(self):
        backend = MagicMock()
        backend.chat.return_value = "标题：测试标题\n简介：测试简介"
        info = self._make_info()
        generate_persona_copy(backend, info)
        call_args = backend.chat.call_args
        messages = call_args.kwargs.get("messages", call_args[0][0] if call_args[0] else [])
        user_msg = messages[-1]["content"]
        assert "搜索策略" not in user_msg

    def test_no_json_schema(self):
        """json_schema should NOT be passed; temperature=1.1 should be."""
        backend = MagicMock()
        backend.chat.return_value = "标题：测试标题\n简介：测试简介"
        info = self._make_info()
        generate_persona_copy(backend, info)
        call_args = backend.chat.call_args
        assert call_args.kwargs.get("json_schema") is None
        assert "json_schema" not in call_args.kwargs

    def test_temperature_is_set(self):
        """backend.chat should be called with temperature=1.1."""
        backend = MagicMock()
        backend.chat.return_value = "标题：测试标题\n简介：测试简介"
        info = self._make_info()
        generate_persona_copy(backend, info)
        call_args = backend.chat.call_args
        assert call_args.kwargs.get("temperature") == 1.1

    def test_link_footer_appended(self):
        backend = MagicMock()
        backend.chat.return_value = "标题：标题\n简介：油管搬的简介正文"
        info = self._make_info()
        result = generate_persona_copy(backend, info)
        assert result["description"].startswith("油管搬的简介正文")
        assert "https://www.youtube.com/watch?v=xyz789" in result["description"]

    def test_view_count_formatted_in_prompt(self):
        backend = MagicMock()
        backend.chat.return_value = "标题：标题\n简介：简介"
        info = self._make_info()  # view_count=4_200_000
        generate_persona_copy(backend, info)
        call_args = backend.chat.call_args
        messages = call_args.kwargs.get("messages", call_args[0][0] if call_args[0] else [])
        user_msg = messages[-1]["content"]
        assert "420万次观看" in user_msg

    def test_empty_description_uses_fallback(self):
        backend = MagicMock()
        backend.chat.return_value = "标题：有效标题\n简介："
        info = self._make_info()
        result = generate_persona_copy(backend, info)
        assert result["title"] == "有效标题"
        assert "本视频搬运自YouTube" in result["description"]

    def test_category_name_in_prompt(self):
        backend = MagicMock()
        backend.chat.return_value = "标题：标题\n简介：简介"
        info = self._make_info()  # category_id=20 → 游戏
        generate_persona_copy(backend, info)
        call_args = backend.chat.call_args
        messages = call_args.kwargs.get("messages", call_args[0][0] if call_args[0] else [])
        user_msg = messages[-1]["content"]
        assert "游戏" in user_msg

    def test_examples_are_sampled(self):
        """Messages should have 3 dialogue-turn example pairs (user+assistant)."""
        backend = MagicMock()
        backend.chat.return_value = "标题：测试标题\n简介：测试简介"
        info = self._make_info()
        generate_persona_copy(backend, info, strategy_name="gaming_deep_dive")
        call_args = backend.chat.call_args
        messages = call_args.kwargs.get("messages", call_args[0][0] if call_args[0] else [])
        # Count assistant messages containing "标题："
        assistant_examples = [
            m for m in messages
            if m["role"] == "assistant" and "标题：" in m["content"]
        ]
        assert len(assistant_examples) == 3
        # First message should be system, last should be user
        assert messages[0]["role"] == "system"
        assert messages[-1]["role"] == "user"
        # Total messages: 1 system + 3*(user+assistant) + 1 user = 8
        assert len(messages) == 8

    def test_no_catchphrase_in_hints(self):
        """STRATEGY_HINTS should not contain literal catchphrases."""
        all_hints = " ".join(STRATEGY_HINTS.values())
        assert "美国佬急了" not in all_hints
        assert "又是经典操作" not in all_hints
        assert "经典双标" not in all_hints

    def test_examples_pool_has_10(self):
        """The examples pool should have 10 entries."""
        assert len(_PERSONA_EXAMPLES) == 10

    def test_examples_are_dicts(self):
        """Each example should be a dict with 'input' and 'output' keys."""
        for i, ex in enumerate(_PERSONA_EXAMPLES):
            assert isinstance(ex, dict), f"Example {i} is not a dict"
            assert "input" in ex, f"Example {i} missing 'input'"
            assert "output" in ex, f"Example {i} missing 'output'"
            assert "标题：" in ex["output"], f"Example {i} output missing '标题：'"
            assert "简介：" in ex["output"], f"Example {i} output missing '简介：'"
