"""
Tests for bilibili_subtitle module (SRT -> BCC conversion and cookie loading).
"""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.bilibili_subtitle import load_cookies, srt_to_bcc


# ── SRT to BCC conversion ────────────────────────────────────────────

SAMPLE_SRT = """\
1
00:00:01,000 --> 00:00:04,500
Hello, welcome to this video.

2
00:00:05,000 --> 00:00:09,200
Today we will talk about Python.

3
00:00:10,000 --> 00:00:15,750
Let's get started!
"""

SAMPLE_SRT_WITH_HTML = """\
1
00:00:01,000 --> 00:00:04,500
<font color="#ffffff">Hello</font> world

2
00:00:05,000 --> 00:00:09,200
<i>Italic text</i> here
"""


class TestSrtToBcc:
    def test_basic_conversion(self):
        bcc = srt_to_bcc(SAMPLE_SRT)
        assert "body" in bcc
        assert len(bcc["body"]) == 3

        first = bcc["body"][0]
        assert first["from"] == 1.0
        assert first["to"] == 4.5
        assert first["content"] == "Hello, welcome to this video."
        assert first["location"] == 2

    def test_timestamps(self):
        bcc = srt_to_bcc(SAMPLE_SRT)
        entries = bcc["body"]
        assert entries[1]["from"] == 5.0
        assert entries[1]["to"] == 9.2
        assert entries[2]["from"] == 10.0
        assert entries[2]["to"] == 15.75

    def test_html_stripped(self):
        bcc = srt_to_bcc(SAMPLE_SRT_WITH_HTML)
        assert len(bcc["body"]) == 2
        assert bcc["body"][0]["content"] == "Hello world"
        assert bcc["body"][1]["content"] == "Italic text here"

    def test_empty_srt(self):
        bcc = srt_to_bcc("")
        assert bcc["body"] == []

    def test_malformed_srt(self):
        bcc = srt_to_bcc("not a real subtitle file\nwith random lines\n")
        assert bcc["body"] == []

    def test_bcc_format_fields(self):
        bcc = srt_to_bcc(SAMPLE_SRT)
        assert "font_size" in bcc
        assert "font_color" in bcc
        assert "background_alpha" in bcc

    def test_multiline_content(self):
        srt = """\
1
00:00:01,000 --> 00:00:04,500
Line one
Line two
"""
        bcc = srt_to_bcc(srt)
        assert len(bcc["body"]) == 1
        assert bcc["body"][0]["content"] == "Line one Line two"


# ── Cookie loading ────────────────────────────────────────────────────


class TestLoadCookies:
    def test_load_valid_cookies(self):
        cookie_data = {
            "cookie_info": {
                "cookies": [
                    {"name": "SESSDATA", "value": "test_sess"},
                    {"name": "bili_jct", "value": "test_csrf"},
                    {"name": "DedeUserID", "value": "12345"},
                ]
            }
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(cookie_data, f)
            path = f.name

        try:
            result = load_cookies(path)
            assert result["SESSDATA"] == "test_sess"
            assert result["bili_jct"] == "test_csrf"
        finally:
            os.unlink(path)

    def test_missing_sessdata(self):
        cookie_data = {
            "cookie_info": {
                "cookies": [
                    {"name": "bili_jct", "value": "test_csrf"},
                ]
            }
        }
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(cookie_data, f)
            path = f.name

        try:
            with pytest.raises(KeyError, match="SESSDATA"):
                load_cookies(path)
        finally:
            os.unlink(path)

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_cookies("/nonexistent/path/cookies.json")
