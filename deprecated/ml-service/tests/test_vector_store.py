"""Tests for VectorStore."""
import os

import numpy as np
import pytest

from app.embeddings.vector_store import VectorStore, RAG_FEATURE_KEYS


def _build_store(n=10, dim=128):
    """Build a VectorStore with n random entries."""
    rng = np.random.RandomState(42)
    embeddings = rng.randn(n, dim).astype(np.float32)
    bvids = [f"BV{i}" for i in range(n)]
    log_views = rng.uniform(5.0, 15.0, size=n).astype(np.float32)
    channel_ids = [f"ch{i % 3}" for i in range(n)]

    store = VectorStore()
    store.build(embeddings, bvids, log_views, channel_ids)
    return store, embeddings, bvids, log_views, channel_ids


class TestVectorStoreBuild:
    def test_size(self):
        store, *_ = _build_store(n=10)
        assert store.size == 10

    def test_empty_store(self):
        store = VectorStore()
        assert store.size == 0


class TestVectorStoreQuery:
    def test_returns_all_rag_keys(self):
        store, embeddings, *_ = _build_store()
        result = store.query(embeddings[0])
        assert set(result.keys()) == set(RAG_FEATURE_KEYS)

    def test_values_are_floats(self):
        store, embeddings, *_ = _build_store()
        result = store.query(embeddings[0])
        for v in result.values():
            assert isinstance(v, float)

    def test_exclude_bvid(self):
        store, embeddings, bvids, *_ = _build_store()
        # Query with the first embedding, excluding itself
        result = store.query(embeddings[0], exclude_bvid="BV0")
        # Should still return valid features
        assert result["rag_similar_mean_log_views"] > 0

    def test_exclude_channel(self):
        store, embeddings, bvids, log_views, channel_ids = _build_store(n=10)
        # Exclude channel "ch0" — indices 0, 3, 6, 9
        result = store.query(embeddings[0], exclude_channel="ch0")
        assert result["rag_similar_mean_log_views"] > 0

    def test_exclude_all_returns_defaults(self):
        """If all entries are excluded, return defaults (all 0.0)."""
        store, embeddings, bvids, log_views, channel_ids = _build_store(n=3)
        # All 3 are in channels ch0, ch1, ch2 — exclude ch0 and the video itself
        # Build a store where all entries are same channel
        small_store = VectorStore()
        small_store.build(
            embeddings[:3],
            ["BV0", "BV1", "BV2"],
            log_views[:3],
            ["ch_same", "ch_same", "ch_same"],
        )
        result = small_store.query(embeddings[0], exclude_channel="ch_same")
        for v in result.values():
            assert v == 0.0

    def test_empty_store_returns_defaults(self):
        store = VectorStore()
        query = np.random.randn(128).astype(np.float32)
        result = store.query(query)
        for v in result.values():
            assert v == 0.0

    def test_top5_mean_uses_fewer_if_needed(self):
        """top5 mean should work with fewer than 5 results."""
        store, embeddings, *_ = _build_store(n=3)
        result = store.query(embeddings[0], top_k=3)
        assert result["rag_top5_mean_log_views"] > 0

    def test_std_is_nonnegative(self):
        store, embeddings, *_ = _build_store()
        result = store.query(embeddings[0])
        assert result["rag_similar_std_log_views"] >= 0

    def test_max_ge_mean(self):
        store, embeddings, *_ = _build_store()
        result = store.query(embeddings[0])
        assert result["rag_similar_max_log_views"] >= result["rag_similar_mean_log_views"]


class TestVectorStoreSaveLoad:
    def test_roundtrip(self, tmp_path):
        store, embeddings, bvids, log_views, channel_ids = _build_store()
        path = str(tmp_path / "test_store.npz")
        store.save(path)
        assert os.path.exists(path)

        loaded = VectorStore.load(path)
        assert loaded.size == store.size
        np.testing.assert_array_almost_equal(loaded.embeddings, store.embeddings)
        np.testing.assert_array_equal(loaded.bvids, store.bvids)

    def test_save_empty_raises(self):
        store = VectorStore()
        with pytest.raises(ValueError, match="empty"):
            store.save("dummy.npz")

    def test_loaded_store_queries_correctly(self, tmp_path):
        store, embeddings, *_ = _build_store()
        path = str(tmp_path / "test_store.npz")
        store.save(path)
        loaded = VectorStore.load(path)

        original = store.query(embeddings[0])
        loaded_result = loaded.query(embeddings[0])
        for key in RAG_FEATURE_KEYS:
            assert original[key] == pytest.approx(loaded_result[key], rel=1e-5)
