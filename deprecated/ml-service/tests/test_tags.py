"""Tests for the tag fetching and aggregation module."""
import os
import sys
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.tags import (
    BilibiliTag,
    fetch_video_tags,
    fetch_tags_for_videos,
    pick_best_tags,
    generate_tags_from_similar,
)


# ── BilibiliTag dataclass ────────────────────────────────────────────


class TestBilibiliTag:
    def test_create_with_defaults(self):
        t = BilibiliTag(tag_name="游戏")
        assert t.tag_name == "游戏"
        assert t.tag_id == 0

    def test_create_with_id(self):
        t = BilibiliTag(tag_name="科技", tag_id=12345)
        assert t.tag_name == "科技"
        assert t.tag_id == 12345


# ── fetch_video_tags ─────────────────────────────────────────────────


class TestFetchVideoTags:
    @pytest.mark.asyncio
    async def test_success(self):
        """Parses tags from a successful API response."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "code": 0,
            "data": [
                {"tag_name": "游戏", "tag_id": 100},
                {"tag_name": "我的世界", "tag_id": 200},
                {"tag_name": "Minecraft", "tag_id": 300},
            ],
        }

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.tags.httpx.AsyncClient", return_value=mock_client):
            tags = await fetch_video_tags("BV1test123")

        assert len(tags) == 3
        assert tags[0].tag_name == "游戏"
        assert tags[0].tag_id == 100
        assert tags[2].tag_name == "Minecraft"

    @pytest.mark.asyncio
    async def test_api_error_code(self):
        """Returns empty list when API returns non-zero code."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"code": -400, "message": "bad request"}

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.tags.httpx.AsyncClient", return_value=mock_client):
            tags = await fetch_video_tags("BV_invalid")

        assert tags == []

    @pytest.mark.asyncio
    async def test_network_error(self):
        """Returns empty list on network error."""
        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("Connection timeout")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.tags.httpx.AsyncClient", return_value=mock_client):
            tags = await fetch_video_tags("BV1test123")

        assert tags == []

    @pytest.mark.asyncio
    async def test_empty_data(self):
        """Returns empty list when data array is empty."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"code": 0, "data": []}

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.tags.httpx.AsyncClient", return_value=mock_client):
            tags = await fetch_video_tags("BV1test123")

        assert tags == []

    @pytest.mark.asyncio
    async def test_strips_whitespace(self):
        """Strips whitespace from tag names."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "code": 0,
            "data": [
                {"tag_name": "  游戏  ", "tag_id": 1},
                {"tag_name": "", "tag_id": 2},
            ],
        }

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.tags.httpx.AsyncClient", return_value=mock_client):
            tags = await fetch_video_tags("BV1test123")

        # Empty tag name should be filtered out
        assert len(tags) == 1
        assert tags[0].tag_name == "游戏"


# ── fetch_tags_for_videos ────────────────────────────────────────────


class TestFetchTagsForVideos:
    @pytest.mark.asyncio
    async def test_fetches_multiple(self):
        """Fetches tags for multiple videos concurrently."""
        async def mock_fetch(bvid):
            if bvid == "BV1":
                return [BilibiliTag("游戏"), BilibiliTag("科技")]
            elif bvid == "BV2":
                return [BilibiliTag("游戏"), BilibiliTag("搞笑")]
            return []

        with patch("app.tags.fetch_video_tags", side_effect=mock_fetch):
            result = await fetch_tags_for_videos(["BV1", "BV2", "BV3"])

        assert len(result) == 3
        assert len(result["BV1"]) == 2
        assert len(result["BV2"]) == 2
        assert len(result["BV3"]) == 0

    @pytest.mark.asyncio
    async def test_handles_exceptions(self):
        """Handles exceptions from individual fetch calls."""
        async def mock_fetch(bvid):
            if bvid == "BV_bad":
                raise Exception("network error")
            return [BilibiliTag("标签")]

        with patch("app.tags.fetch_video_tags", side_effect=mock_fetch):
            result = await fetch_tags_for_videos(["BV1", "BV_bad"])

        assert len(result["BV1"]) == 1
        assert result["BV_bad"] == []


# ── pick_best_tags ───────────────────────────────────────────────────


class TestPickBestTags:
    def test_frequency_ranking(self):
        """Tags appearing in more videos rank higher."""
        tags_by_video = {
            "BV1": [BilibiliTag("游戏"), BilibiliTag("科技"), BilibiliTag("日常")],
            "BV2": [BilibiliTag("游戏"), BilibiliTag("搞笑")],
            "BV3": [BilibiliTag("游戏"), BilibiliTag("科技")],
        }
        result = pick_best_tags(tags_by_video)
        assert result[0] == "游戏"  # appears in 3 videos
        assert result[1] == "科技"  # appears in 2 videos

    def test_max_tags_limit(self):
        """Respects max_tags parameter."""
        tags_by_video = {
            "BV1": [BilibiliTag(f"tag{i}") for i in range(20)],
        }
        result = pick_best_tags(tags_by_video, max_tags=5)
        assert len(result) == 5

    def test_empty_input(self):
        """Returns empty list for empty input."""
        assert pick_best_tags({}) == []

    def test_no_duplicate_counting(self):
        """Same tag in one video is counted only once."""
        tags_by_video = {
            "BV1": [BilibiliTag("游戏"), BilibiliTag("游戏"), BilibiliTag("游戏")],
            "BV2": [BilibiliTag("科技")],
        }
        result = pick_best_tags(tags_by_video)
        # 游戏 appears in 1 video (not 3), 科技 in 1 video — tied
        assert len(result) == 2
        assert "游戏" in result
        assert "科技" in result

    def test_all_empty_tags(self):
        """Returns empty list when no videos have tags."""
        tags_by_video = {
            "BV1": [],
            "BV2": [],
        }
        assert pick_best_tags(tags_by_video) == []


# ── generate_tags_from_similar ───────────────────────────────────────


class TestGenerateTagsFromSimilar:
    @pytest.mark.asyncio
    async def test_end_to_end(self):
        """Full pipeline: search -> fetch tags -> pick best."""
        from app.web_rag.bilibili_search import BilibiliSearchResult

        mock_search_results = [
            BilibiliSearchResult(bvid="BV1", title="T1", author="A1",
                                 views=10000, likes=500, danmaku=100,
                                 duration_seconds=300),
            BilibiliSearchResult(bvid="BV2", title="T2", author="A2",
                                 views=20000, likes=1000, danmaku=200,
                                 duration_seconds=600),
        ]

        async def mock_fetch(bvid):
            if bvid == "BV1":
                return [BilibiliTag("游戏"), BilibiliTag("我的世界")]
            elif bvid == "BV2":
                return [BilibiliTag("游戏"), BilibiliTag("Minecraft")]
            return []

        with patch("app.web_rag.bilibili_search.search_bilibili_similar",
                    new_callable=AsyncMock, return_value=mock_search_results):
            with patch("app.tags.fetch_video_tags", side_effect=mock_fetch):
                tags = await generate_tags_from_similar("我的世界")

        assert tags[0] == "游戏"  # appears in both videos
        assert len(tags) == 3

    @pytest.mark.asyncio
    async def test_no_similar_videos(self):
        """Returns empty when no similar videos found."""
        with patch("app.web_rag.bilibili_search.search_bilibili_similar",
                    new_callable=AsyncMock, return_value=[]):
            tags = await generate_tags_from_similar("obscure query")

        assert tags == []

    @pytest.mark.asyncio
    async def test_search_failure(self):
        """Returns empty on search failure."""
        with patch("app.web_rag.bilibili_search.search_bilibili_similar",
                    new_callable=AsyncMock, side_effect=Exception("API down")):
            tags = await generate_tags_from_similar("any query")

        assert tags == []
