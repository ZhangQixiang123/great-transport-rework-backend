"""Multi-source video search — consolidates web_rag/ and discovery/ search modules.

Phase 1: re-exports from existing modules for backward compatibility.
"""

# Re-export from existing web_rag module
from ..web_rag.bilibili_search import BilibiliSearchResult, search_bilibili_similar
from ..web_rag.youtube_similar import YouTubeSimilarResult, search_youtube_similar
from ..web_rag.aggregator import WebRAGAggregator, WebRAGContext

from .aggregator import SearchAggregator
