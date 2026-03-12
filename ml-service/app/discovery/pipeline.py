"""
Discovery pipeline orchestrator — strategy-driven LLM discovery.

Three phases:
  A. Strategy-driven topic discovery (LLM generates YouTube queries)
  B. YouTube search + filter (dedup, quality, novelty)
  C. Evaluate candidates (VectorStore + neural predictor + LLM final eval)
"""
import logging
import math
import os
from typing import Optional

import numpy as np

from ..db.database import Database
from ..llm.backend import LLMBackend, create_backend
from .llm_scorer import LLMScorer
from .models import Recommendation, YouTubeCandidate
from .strategies import (
    TRANSPORT_STRATEGIES,
    TransportStrategy,
    check_query_saturation,
    check_strategy_saturation,
    get_unsaturated_strategies,
)
from .topic_generator import TopicGenerator
from .trending import fetch_trending_keywords
from .youtube_search import search_youtube_videos

logger = logging.getLogger(__name__)


class DiscoveryPipeline:
    """Orchestrates strategy-driven video discovery."""

    def __init__(
        self,
        db: Database,
        model_dir: str = "models",
        llm_model: str = "qwen2.5:7b",
        backend_type: str = "ollama",
    ):
        self.db = db
        self.model_dir = model_dir
        self.backend_type = backend_type

        # Create a shared LLM backend
        self._backend = create_backend(backend_type=backend_type, model=llm_model)
        self.scorer = LLMScorer(backend=self._backend)
        self.topic_generator = TopicGenerator(backend=self._backend)

        # Lazy-load models
        self._ranker = None
        self._neural_predictor = None
        self._vector_store = None
        self._embedder = None
        self._web_rag = None

    def _get_ranker(self):
        """Load LightGBM ranker model on first use."""
        if self._ranker is None:
            try:
                from ..models.ranker import RankerModel
                self._ranker = RankerModel.load_latest(self.model_dir)
                logger.info("Loaded ranker model from %s", self.model_dir)
            except FileNotFoundError:
                logger.warning("No trained LightGBM model found in %s", self.model_dir)
        return self._ranker

    def _get_neural_predictor(self):
        """Load neural predictor on first use."""
        if self._neural_predictor is None:
            predictor_path = os.path.join(self.model_dir, "predictor.pt")
            # Fallback to old reranker.pt for backwards compat
            if not os.path.exists(predictor_path):
                predictor_path = os.path.join(self.model_dir, "reranker.pt")
            if os.path.exists(predictor_path):
                try:
                    from ..prediction.neural_reranker import NeuralPredictor
                    self._neural_predictor = NeuralPredictor.load(predictor_path)
                    self._neural_predictor.eval()
                    logger.info("Loaded neural predictor from %s", predictor_path)
                except Exception as e:
                    logger.warning("Failed to load neural predictor: %s", e)
        return self._neural_predictor

    def _get_vector_store(self):
        """Load VectorStore on first use."""
        if self._vector_store is None:
            vs_path = os.path.join(self.model_dir, "vector_store.npz")
            if os.path.exists(vs_path):
                try:
                    from ..embeddings.vector_store import VectorStore
                    self._vector_store = VectorStore.load(vs_path)
                    logger.info("Loaded VectorStore from %s", vs_path)
                except Exception as e:
                    logger.warning("Failed to load VectorStore: %s", e)
        return self._vector_store

    def _get_embedder(self):
        """Load TitleEmbedder on first use."""
        if self._embedder is None:
            emb_path = os.path.join(self.model_dir, "embedder.pt")
            if os.path.exists(emb_path):
                try:
                    from ..embeddings.model import TitleEmbedder
                    self._embedder = TitleEmbedder.load(emb_path)
                    logger.info("Loaded TitleEmbedder from %s", emb_path)
                except Exception as e:
                    logger.warning("Failed to load TitleEmbedder: %s", e)
        return self._embedder

    def _get_web_rag(self):
        """Get web RAG aggregator on first use."""
        if self._web_rag is None:
            try:
                from ..web_rag.aggregator import WebRAGAggregator
                self._web_rag = WebRAGAggregator()
                logger.info("Initialized web RAG aggregator")
            except Exception as e:
                logger.warning("Failed to create web RAG aggregator: %s", e)
        return self._web_rag

    async def run(
        self,
        max_keywords: int = 10,
        videos_per_keyword: int = 5,
        max_age_days: int = 30,
    ) -> list[Recommendation]:
        """Run the full strategy-driven discovery pipeline.

        Phases:
            A. Discover topics: strategies + saturation + LLM query generation
            B. Search YouTube + filter duplicates + novelty check
            C. Evaluate candidates: VectorStore + neural predictor + LLM final eval

        Args:
            max_keywords: Max trending keywords to fetch (demand signals).
            videos_per_keyword: Max YouTube videos per generated query.
            max_age_days: Only consider YouTube videos within N days.

        Returns:
            Ranked list of Recommendations.
        """
        # Load already-transported video IDs
        self.db.ensure_discovery_tables()
        already_seen = self.db.get_already_transported_yt_ids()
        if already_seen:
            logger.info("Loaded %d already-transported video IDs", len(already_seen))

        # ── Phase A: Discover Topics ──
        logger.info("Phase A: Strategy-driven topic discovery...")
        queries = await self._discover_topics(max_keywords)

        if not queries:
            logger.warning("No queries generated")
            return []

        logger.info("Generated %d search queries", len(queries))

        # ── Phase B: Search + Filter ──
        logger.info("Phase B: YouTube search + filtering...")
        candidates = await self._search_and_filter(
            queries, already_seen, videos_per_keyword, max_age_days,
        )

        if not candidates:
            logger.warning("No candidates after filtering")
            return []

        logger.info("Got %d candidates after filtering", len(candidates))

        # ── Phase C: Evaluate Candidates ──
        logger.info("Phase C: Evaluating candidates...")
        recommendations = await self._evaluate_candidates(candidates)

        # Sort by combined score
        recommendations.sort(key=lambda r: r.combined_score, reverse=True)

        # Save results
        run_id = self.db.save_discovery_run(
            keywords_fetched=max_keywords,
            candidates_found=len(candidates),
            recommendations_count=len(recommendations),
        )
        self.db.save_recommendations(run_id, recommendations)

        logger.info(
            "Pipeline complete: %d queries, %d candidates, %d recommendations",
            len(queries), len(candidates), len(recommendations),
        )
        return recommendations

    async def _discover_topics(self, max_keywords: int) -> list[dict]:
        """Phase A: Generate YouTube search queries from strategies.

        Returns:
            List of dicts with 'query', 'strategy_name', 'bilibili_check'.
        """
        from ..web_rag.bilibili_search import search_bilibili_similar

        # Step 1: Fetch Bilibili hot words (demand signals)
        keywords = await fetch_trending_keywords()
        keywords = keywords[:max_keywords]
        hot_words = [
            {"keyword": kw.keyword, "heat_score": kw.heat_score}
            for kw in keywords
        ]

        # Step 2a: Check strategy saturation
        strategies = [
            TransportStrategy(
                name=s.name,
                description=s.description,
                example_queries=list(s.example_queries),
                bilibili_check=s.bilibili_check,
            )
            for s in TRANSPORT_STRATEGIES
        ]

        for strategy in strategies:
            await check_strategy_saturation(
                strategy, search_bilibili_similar,
            )

        unsaturated = get_unsaturated_strategies(strategies)
        logger.info(
            "%d/%d strategies are unsaturated",
            len(unsaturated), len(strategies),
        )

        # Step 2b: Get past successes for context
        past_successes = self._get_past_successes()

        # Step 2c: LLM generates queries
        generated = self.topic_generator.generate_queries(
            unsaturated, hot_words, past_successes,
        )

        if not generated:
            # Fallback: use strategy example queries directly
            logger.warning("LLM query generation failed, using strategy examples")
            return [
                {"query": q, "strategy_name": s.name, "bilibili_check": s.bilibili_check}
                for s in unsaturated[:4]
                for q in s.example_queries
            ]

        # Step 2d: Per-query saturation check
        validated_queries = []
        saturated_queries = []

        for gq in generated:
            is_saturated = await check_query_saturation(
                gq.query, gq.bilibili_check, search_bilibili_similar,
            )
            if not is_saturated:
                validated_queries.append({
                    "query": gq.query,
                    "strategy_name": gq.strategy_name,
                    "bilibili_check": gq.bilibili_check,
                })
            else:
                saturated_queries.append(gq)

        # Refinement loop: if many queries saturated, ask LLM for alternatives
        if saturated_queries and len(validated_queries) < 5:
            refined = self.topic_generator.refine_queries(saturated_queries)
            for gq in refined:
                is_saturated = await check_query_saturation(
                    gq.query, gq.bilibili_check, search_bilibili_similar,
                )
                if not is_saturated:
                    validated_queries.append({
                        "query": gq.query,
                        "strategy_name": gq.strategy_name,
                        "bilibili_check": gq.bilibili_check,
                    })

        logger.info(
            "Query validation: %d validated, %d saturated",
            len(validated_queries), len(saturated_queries),
        )
        return validated_queries

    async def _search_and_filter(
        self,
        queries: list[dict],
        already_seen: set,
        videos_per_query: int,
        max_age_days: int,
    ) -> list[dict]:
        """Phase B: Search YouTube and filter candidates.

        Returns:
            List of dicts with 'candidate' (YouTubeCandidate), 'strategy',
            'query', 'novelty_info'.
        """
        from ..web_rag.bilibili_search import search_bilibili_similar

        seen_video_ids: set[str] = set()
        candidates = []

        for q in queries:
            results = search_youtube_videos(
                q["query"], max_results=videos_per_query, max_age_days=max_age_days,
            )
            for candidate in results:
                # Skip duplicates
                if candidate.video_id in seen_video_ids:
                    continue
                seen_video_ids.add(candidate.video_id)

                # Skip already-transported
                if candidate.video_id in already_seen:
                    continue

                # Novelty check: search Bilibili for similar content
                novelty_info = await self._check_novelty(
                    candidate, search_bilibili_similar,
                )

                candidates.append({
                    "candidate": candidate,
                    "strategy": q["strategy_name"],
                    "query": q["query"],
                    "novelty_info": novelty_info,
                })

        return candidates

    async def _check_novelty(self, candidate: YouTubeCandidate, search_fn) -> dict:
        """Check if similar content already exists on Bilibili.

        Returns:
            Dict with novelty_score (0-1), similar_count, top_similar.
        """
        try:
            # Search Bilibili with the video title
            results = await search_fn(candidate.title[:50], 10)
            high_view_results = [r for r in results if r.views >= 5000]
            similar_count = len(high_view_results)

            # Novelty: fewer existing videos = more novel
            novelty_score = max(0.0, 1.0 - similar_count / 5.0)

            return {
                "novelty_score": novelty_score,
                "similar_count": similar_count,
                "top_similar": [
                    {"title": r.title, "views": r.views, "bvid": r.bvid}
                    for r in sorted(high_view_results, key=lambda x: x.views, reverse=True)[:3]
                ],
            }
        except Exception as e:
            logger.warning("Novelty check failed for '%s': %s", candidate.title[:40], e)
            return {"novelty_score": 1.0, "similar_count": 0, "top_similar": []}

    async def _evaluate_candidates(
        self,
        candidates: list[dict],
    ) -> list[Recommendation]:
        """Phase C: Evaluate each candidate with VectorStore + NN + LLM.

        Returns:
            List of Recommendation objects.
        """
        recommendations = []

        # 5a+5b. Batch VectorStore lookups and neural predictions
        all_candidates = [item["candidate"] for item in candidates]
        all_vs_examples = [self._query_vectorstore(c.title) for c in all_candidates]
        nn_preds = self._predict_neural_batch(all_candidates, all_vs_examples)

        for idx, item in enumerate(candidates):
            candidate = item["candidate"]
            novelty_info = item["novelty_info"]
            strategy = item["strategy"]
            query = item["query"]

            vs_examples = all_vs_examples[idx]
            nn_pred = nn_preds[idx]

            # 5c. LLM final evaluation
            evaluation = self.scorer.evaluate_candidate(
                candidate, nn_pred, vs_examples, novelty_info,
            )

            if evaluation is not None:
                pred_log_views = evaluation.predicted_log_views
                pred_views = evaluation.predicted_views
                pred_label = evaluation.label
                confidence = evaluation.confidence
                reasoning = evaluation.reasoning
            elif nn_pred is not None:
                # Fallback to neural predictor
                pred_log_views = nn_pred
                pred_views = math.expm1(nn_pred)
                from ..prediction.models import VideoPredictionResult
                pred_label = VideoPredictionResult.label_from_log_views(nn_pred)
                confidence = 0.5
                reasoning = "Neural predictor only (LLM evaluation unavailable)"
            else:
                pred_log_views = None
                pred_views = None
                pred_label = None
                confidence = 0.0
                reasoning = "No prediction available"

            # Compute combined score
            combined = self._compute_combined_score(
                predicted_views=pred_views,
                novelty_score=novelty_info.get("novelty_score", 1.0),
                confidence=confidence,
            )

            rec = Recommendation(
                strategy=strategy,
                query_used=query,
                youtube_video_id=candidate.video_id,
                youtube_title=candidate.title,
                youtube_channel=candidate.channel_title,
                youtube_views=candidate.views,
                youtube_likes=candidate.likes,
                youtube_duration_seconds=candidate.duration_seconds,
                nn_prediction=nn_pred,
                novelty_score=novelty_info.get("novelty_score", 1.0),
                predicted_log_views=pred_log_views,
                predicted_views=pred_views,
                predicted_label=pred_label,
                confidence=confidence,
                reasoning=reasoning,
                combined_score=combined,
            )
            recommendations.append(rec)

        return recommendations

    def _query_vectorstore(self, title: str) -> list[dict]:
        """Query VectorStore for similar past transports."""
        embedder = self._get_embedder()
        vector_store = self._get_vector_store()

        if embedder is None or vector_store is None:
            return []

        try:
            emb = embedder.encode([title])[0]
            return vector_store.query_detailed(emb, top_k=5)
        except Exception as e:
            logger.warning("VectorStore query failed: %s", e)
            return []

    def _predict_neural_batch(
        self,
        candidates: list[YouTubeCandidate],
        vs_examples_list: list[list[dict]],
    ) -> list[Optional[float]]:
        """Run neural predictor on a batch of candidates.

        Returns:
            List of predicted log_views (or None per candidate on failure).
        """
        predictor = self._get_neural_predictor()
        if predictor is None:
            return [None] * len(candidates)

        try:
            import torch
            from ..prediction.dataset import CANDIDATE_NUMERIC_KEYS
            from ..prediction.neural_reranker import (
                MAX_SIMILAR_VIDEOS,
                SIMILAR_VIDEO_DIM,
                TITLE_EMBEDDING_DIM,
                duration_to_bucket,
            )

            device = next(predictor.parameters()).device
            predictor.eval()
            n = len(candidates)

            # Build numeric features for all candidates
            numeric_rows = []
            for candidate in candidates:
                features = {
                    "yt_log_views": math.log1p(candidate.views),
                    "yt_log_likes": math.log1p(candidate.likes),
                    "yt_log_comments": math.log1p(candidate.comments),
                    "yt_duration_seconds": float(candidate.duration_seconds),
                    "yt_like_view_ratio": candidate.likes / max(candidate.views, 1),
                    "yt_comment_view_ratio": candidate.comments / max(candidate.views, 1),
                    "publish_hour_sin": 0.0,
                    "publish_hour_cos": 1.0,
                    "publish_dow_sin": 0.0,
                    "publish_dow_cos": 1.0,
                    "title_length": float(len(candidate.title)),
                    "title_exclamation_count": float(candidate.title.count("!")),
                    "title_caps_ratio": 0.0,
                    "heat_score": 0.0,
                    "relevance_score": 0.5,
                }
                numeric_rows.append([features.get(k, 0.0) for k in CANDIDATE_NUMERIC_KEYS])

            candidate_numeric = torch.tensor(numeric_rows, dtype=torch.float32, device=device)

            # Title embeddings — batch encode all titles at once
            embedder = self._get_embedder()
            if embedder is not None:
                all_embs = embedder.encode([c.title for c in candidates])
                title_emb = torch.tensor(all_embs, dtype=torch.float32, device=device)
            else:
                title_emb = torch.zeros(n, TITLE_EMBEDDING_DIM, device=device)

            # Categorical
            category_id = torch.tensor(
                [c.category_id % 50 for c in candidates], dtype=torch.long, device=device,
            )
            duration_bucket = torch.tensor(
                [duration_to_bucket(c.duration_seconds) for c in candidates],
                dtype=torch.long, device=device,
            )

            # Similar videos — pad into [N, MAX_SIMILAR, 3]
            similar_features = torch.zeros(n, MAX_SIMILAR_VIDEOS, SIMILAR_VIDEO_DIM, device=device)
            similar_mask = torch.ones(n, MAX_SIMILAR_VIDEOS, dtype=torch.bool, device=device)

            for i, vs_examples in enumerate(vs_examples_list):
                for j, sv in enumerate(vs_examples[:MAX_SIMILAR_VIDEOS]):
                    similar_features[i, j] = torch.tensor([
                        sv["log_views"],
                        sv.get("similarity", 0.0),
                        float(sv.get("rank", j + 1)),
                    ], device=device)
                    similar_mask[i, j] = False

            with torch.inference_mode():
                preds = predictor(
                    candidate_numeric, title_emb, category_id,
                    duration_bucket, similar_features, similar_mask,
                ).squeeze(-1)

            return [float(p) for p in preds.cpu()]

        except Exception as e:
            logger.warning("Batched neural prediction failed: %s", e)
            return [None] * len(candidates)

    def _compute_combined_score(
        self,
        predicted_views: Optional[float],
        novelty_score: float,
        confidence: float,
    ) -> float:
        """Combine signals into a final ranking score.

        Score = normalized_views * novelty * confidence

        Args:
            predicted_views: Predicted Bilibili views (may be None).
            novelty_score: 0.0-1.0 novelty on Bilibili.
            confidence: 0.0-1.0 LLM confidence.

        Returns:
            Combined score in [0, 1].
        """
        # Normalize predicted views to [0, 1] using log scale
        if predicted_views is not None and predicted_views > 0:
            norm_views = min(1.0, math.log1p(predicted_views) / math.log1p(1_000_000))
        else:
            norm_views = 0.3  # neutral default

        # Combined: views * novelty * confidence
        return norm_views * novelty_score * max(confidence, 0.1)

    def _get_past_successes(self) -> list[dict]:
        """Get top past transports from VectorStore for LLM context."""
        vs = self._get_vector_store()
        if vs is None or vs.size == 0:
            return []

        # Get top videos by views from the store
        try:
            top_indices = np.argsort(vs.log_views)[::-1][:10]
            return [
                {
                    "title": str(vs.bvids[i]),
                    "views": int(math.expm1(vs.log_views[i])),
                }
                for i in top_indices
            ]
        except Exception:
            return []
