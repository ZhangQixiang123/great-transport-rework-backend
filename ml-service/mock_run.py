"""
Mock run: Fetch 1 Bilibili hotword, translate it, search YouTube for 2 videos,
score relevance, predict Bilibili views, and display results.
"""
import asyncio
import logging
import math
import sys
from datetime import datetime

# Force UTF-8 output on Windows
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("mock_run")


async def main():
    # ===== STEP 1: Fetch Bilibili trending hotwords =====
    print("\n" + "=" * 70)
    print("STEP 1: Fetching Bilibili trending hotwords...")
    print("=" * 70)

    from app.discovery.trending import fetch_trending_keywords

    keywords = await fetch_trending_keywords()
    if not keywords:
        print("ERROR: No trending keywords found!")
        return

    print(f"\nFound {len(keywords)} trending keywords:")
    for i, kw in enumerate(keywords[:10]):
        print(f"  {i+1:2d}. [{kw.heat_score:>10,}] {kw.keyword}")

    # Pick the top non-commercial keyword
    chosen = keywords[0]
    print(f"\n>>> Selected hotword: \"{chosen.keyword}\" (heat={chosen.heat_score:,})")

    # ===== STEP 2: Translate keyword to English search queries =====
    print("\n" + "=" * 70)
    print("STEP 2: Translating keyword to English via Ollama (qwen2.5:7b)...")
    print("=" * 70)

    from app.discovery.llm_scorer import LLMScorer

    scorer = LLMScorer(model="qwen2.5:7b")
    translated = scorer.translate_keyword(chosen.keyword)

    if translated is None:
        print("ERROR: Translation failed!")
        return

    print(f"\n  Topic summary: {translated.topic_summary}")
    print(f"  English queries:")
    for q in translated.english_queries:
        print(f"    - \"{q}\"")

    # ===== STEP 3: Search YouTube for 2 videos =====
    print("\n" + "=" * 70)
    print("STEP 3: Searching YouTube for videos (max 2 per query)...")
    print("=" * 70)

    from app.discovery.youtube_search import search_youtube_videos

    all_candidates = []
    seen_ids = set()

    for query in translated.english_queries:
        print(f"\n  Searching: \"{query}\"")
        results = search_youtube_videos(query, max_results=2, max_age_days=30)
        for c in results:
            if c.video_id not in seen_ids:
                seen_ids.add(c.video_id)
                all_candidates.append(c)
                print(f"    + [{c.video_id}] {c.title}")
                print(f"      Channel: {c.channel_title} | Views: {c.views:,} | Likes: {c.likes:,} | Duration: {c.duration_seconds}s")

        # Stop once we have at least 2
        if len(all_candidates) >= 2:
            break

    # Keep only the first 2
    candidates = all_candidates[:2]
    print(f"\n>>> Kept {len(candidates)} candidate videos for scoring")

    if not candidates:
        print("ERROR: No YouTube videos found!")
        return

    # ===== STEP 4: Score relevance with LLM =====
    print("\n" + "=" * 70)
    print("STEP 4: Scoring relevance with Ollama LLM...")
    print("=" * 70)

    scored_candidates = []
    for c in candidates:
        print(f"\n  Scoring: \"{c.title[:60]}\"")
        relevance = scorer.score_relevance(chosen.keyword, c)
        if relevance is None:
            print(f"    -> Scoring failed, skipping")
            continue

        print(f"    -> Relevance: {relevance.relevance_score:.2f} ({'RELEVANT' if relevance.is_relevant else 'NOT RELEVANT'})")
        print(f"    -> Reasoning: {relevance.reasoning}")
        print(f"    -> Topics: {relevance.detected_topics}")
        scored_candidates.append((c, relevance))

    if not scored_candidates:
        print("ERROR: No candidates passed relevance scoring!")
        return

    # ===== STEP 5: Predict Bilibili views with ML model =====
    print("\n" + "=" * 70)
    print("STEP 5: Predicting Bilibili views with LightGBM + RAG model...")
    print("=" * 70)

    from app.discovery.pipeline import _make_dummy_video, _make_yt_stats

    try:
        from app.models.ranker import RankerModel
        ranker = RankerModel.load_latest("models")
        has_ranker = True
        print("  Ranker loaded successfully (48 features, fine-tuned embeddings + RAG)")
    except Exception as e:
        print(f"  Warning: Could not load ranker: {e}")
        has_ranker = False

    results = []
    for c, rel in scored_candidates:
        print(f"\n  Predicting: \"{c.title[:60]}\"")

        pred_log_views = None
        pred_views = None
        pred_label = None

        if has_ranker:
            try:
                dummy = _make_dummy_video(c)
                yt_stats = _make_yt_stats(c)
                prediction = ranker.predict_video(dummy, yt_stats=yt_stats)
                pred_log_views = prediction["predicted_log_views"]
                pred_views = prediction["predicted_views"]
                pred_label = prediction["label"]
                print(f"    -> Predicted log(views): {pred_log_views:.2f}")
                print(f"    -> Predicted views: {pred_views:,.0f}")
                print(f"    -> Label: {pred_label}")
            except Exception as e:
                print(f"    -> Prediction failed: {e}")

        # Compute combined score
        heat_weight, rel_weight, views_weight = 0.2, 0.4, 0.4
        norm_heat = min(1.0, math.log1p(chosen.heat_score) / math.log1p(5_000_000))
        norm_rel = rel.relevance_score
        if pred_views and pred_views > 0:
            norm_views = min(1.0, math.log1p(pred_views) / math.log1p(1_000_000))
        else:
            norm_views = 0.5
        combined = heat_weight * norm_heat + rel_weight * norm_rel + views_weight * norm_views

        results.append({
            "candidate": c,
            "relevance": rel,
            "pred_log_views": pred_log_views,
            "pred_views": pred_views,
            "pred_label": pred_label,
            "combined_score": combined,
        })

    # ===== FINAL RESULTS =====
    print("\n" + "=" * 70)
    print("FINAL RESULTS: Ranked Recommendations")
    print("=" * 70)

    results.sort(key=lambda r: r["combined_score"], reverse=True)

    print(f"\nBilibili Hotword: \"{chosen.keyword}\" (heat={chosen.heat_score:,})")
    print(f"Topic: {translated.topic_summary}\n")

    for i, r in enumerate(results):
        c = r["candidate"]
        rel = r["relevance"]
        print(f"--- Rank #{i+1} (combined_score={r['combined_score']:.3f}) ---")
        print(f"  Title:   {c.title}")
        print(f"  Channel: {c.channel_title}")
        print(f"  YouTube: https://youtube.com/watch?v={c.video_id}")
        print(f"  YT Stats: {c.views:,} views | {c.likes:,} likes | {c.duration_seconds}s")
        print(f"  Relevance: {rel.relevance_score:.2f} - {rel.reasoning}")
        if r["pred_views"]:
            print(f"  Predicted Bilibili: {r['pred_views']:,.0f} views ({r['pred_label']})")
        else:
            print(f"  Predicted Bilibili: N/A")
        print()

    print("=" * 70)
    print("Mock run complete!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
