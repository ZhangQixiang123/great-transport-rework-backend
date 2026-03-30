#!/usr/bin/env python3
"""Real end-to-end discovery run using live APIs.

Calls real LLM backend, real YouTube Data API, and real Bilibili search
to exercise the skill-based discovery pipeline with actual data.

Run:
  cd ml-service
  .venv\\Scripts\\python real_run.py --backend ollama
  .venv\\Scripts\\python real_run.py --backend openai --model gpt-4o
  .venv\\Scripts\\python real_run.py --backend anthropic
  .venv\\Scripts\\python real_run.py --backend ollama --model qwen2.5:14b --max-queries 3
"""
import argparse
import asyncio
import io
import json
import math
import random
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app.db.database import Database
from app.bootstrap import run_bootstrap, refresh_strategies
from app.llm.backend import create_backend
from app.skills.strategy_generation import StrategyGenerationSkill
from app.skills.market_analysis import MarketAnalysisSkill
from app.scoring.heuristic import ScoringParams, heuristic_score
from app.scoring.transportability import check_transportability
from app.search.aggregator import SearchAggregator
from app.outcomes.tracker import OutcomeTracker
from app.discovery.youtube_search import search_youtube_videos
from app.discovery.trending import fetch_trending_keywords
from app.web_rag.bilibili_search import search_bilibili_similar
from app.description import generate_description, translate_title, generate_persona_copy
from app.tags import generate_tags_from_similar
from app.upload_client import UploadClient


# ── YouTube category ID -> name mapping ──────────────────────────────

YT_CATEGORIES = {
    1: "Film & Animation", 2: "Autos & Vehicles", 10: "Music",
    15: "Pets & Animals", 17: "Sports", 19: "Travel & Events",
    20: "Gaming", 22: "People & Blogs", 23: "Comedy",
    24: "Entertainment", 25: "News & Politics", 26: "Howto & Style",
    27: "Education", 28: "Science & Technology",
}


# ── Formatting helpers ───────────────────────────────────────────────

def fmt_duration(seconds):
    """Format seconds as M:SS or H:MM:SS."""
    if seconds >= 3600:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h}:{m:02d}:{s:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


def print_header(text):
    w = 76
    print(f"\n{'=' * w}")
    print(f"  {text}")
    print(f"{'=' * w}")


def print_section(text):
    print(f"\n{'─' * 40}")
    print(f"  {text}")
    print(f"{'─' * 40}")


def wrap_print(text, indent=4, width=80):
    """Word-wrap text and print with indent."""
    if not isinstance(text, str):
        text = str(text)
    prefix = " " * indent
    words = text.split()
    line = prefix
    for w in words:
        if len(line) + len(w) + 1 > width:
            print(line)
            line = prefix + w
        else:
            line += " " + w if line.strip() else prefix + w
    if line.strip():
        print(line)


def parse_args():
    p = argparse.ArgumentParser(description="Real discovery run with live APIs")
    p.add_argument("--backend", choices=["ollama", "openai", "anthropic"],
                    default="ollama", help="LLM backend (default: ollama)")
    p.add_argument("--model", default=None,
                    help="LLM model name (default: per-backend default)")
    p.add_argument("--db-path", default="data.db",
                    help="SQLite database path (default: data.db)")
    p.add_argument("--max-queries", type=int, default=5,
                    help="Max queries for strategy generation (default: 5)")
    p.add_argument("--max-age-days", type=int, default=60,
                    help="YouTube recency filter in days (default: 60)")
    p.add_argument("--top-n", type=int, default=3,
                    help="Top candidates for transportability check (default: 3)")
    p.add_argument("--skip-reflection", action="store_true",
                    help="Skip Loop 1/2 reflection (saves LLM calls)")
    p.add_argument("--upload", action="store_true",
                    help="Enable upload phase (send top candidates to Go service)")
    p.add_argument("--dry-run", action="store_true",
                    help="Generate titles + descriptions and print payloads, but don't upload")
    p.add_argument("--go-url", default="http://localhost:8080",
                    help="Go upload service URL (default: http://localhost:8080)")
    return p.parse_args()


# ── Main pipeline ────────────────────────────────────────────────────

async def main():
    args = parse_args()
    llm_call_count = 0
    t_start = time.time()

    print_header("Real Discovery Run — Live APIs")
    print(f"  Backend       : {args.backend}")
    print(f"  Model         : {args.model or '(default)'}")
    print(f"  DB            : {args.db_path}")
    print(f"  Max queries   : {args.max_queries}")
    print(f"  Max age days  : {args.max_age_days}")
    print(f"  Top-N         : {args.top_n}")
    print(f"  Skip reflect  : {args.skip_reflection}")

    # ── Create backend ──
    print(f"\n  Creating LLM backend...")
    backend = create_backend(args.backend, model=args.model)
    print(f"  Backend ready: {args.backend}" +
          (f" ({args.model})" if args.model else ""))

    db = Database(args.db_path)
    db.connect()

    try:
        # ================================================================
        # PHASE 0: Bootstrap
        # ================================================================
        print_header("PHASE 0: Bootstrap")
        db.ensure_skill_tables()
        db.ensure_competitor_tables()

        strategies = db.list_strategies()
        if not strategies:
            result = run_bootstrap(db, backend=None, skip_llm=True)
            print(f"  Bootstrapped: {result['strategies_seeded']} strategies, "
                  f"{result['channels_seeded']} channels")
        else:
            print(f"  Already bootstrapped: {len(strategies)} strategies")

        # Refresh strategy metadata from code (updates example_queries etc.)
        refreshed = refresh_strategies(db)
        if refreshed:
            print(f"  Refreshed example_queries for {refreshed} strategies")

        # Load scoring params
        params_row = db.get_scoring_params()
        if params_row:
            params = ScoringParams.from_json(params_row["params_json"])
        else:
            params = ScoringParams()

        print(f"\n  Scoring parameters:")
        print(f"    Bilibili success threshold : {params.bilibili_success_threshold:,} views")
        print(f"    YouTube min views filter    : {params.youtube_min_views:,} views")
        print(f"    Duration sweet spot         : {fmt_duration(params.duration_sweet_spot[0])} – {fmt_duration(params.duration_sweet_spot[1])}")
        print(f"    Engagement threshold        : {params.engagement_good_threshold:.2%} like ratio")
        print(f"    Weights                     : engagement={params.engagement_weight}, "
              f"views={params.view_signal_weight}, "
              f"opportunity={params.opportunity_weight}, "
              f"duration={params.duration_weight}")
        if params.category_bonuses:
            print(f"    Category bonuses:")
            for cat_id, bonus in sorted(params.category_bonuses.items(),
                                        key=lambda x: x[1], reverse=True)[:8]:
                cat_name = YT_CATEGORIES.get(cat_id, f"Category {cat_id}")
                arrow = "▲" if bonus > 1 else "▼" if bonus < 1 else "─"
                print(f"      {cat_name:<25} {bonus:.2f}x {arrow}")

        # ================================================================
        # PHASE A: Strategy Generation (1 LLM call)
        # ================================================================
        print_header("PHASE A: Strategy Generation (LLM Skill)")

        strategies = db.list_strategies(active_only=True)

        # Strategy sampling: pick a random subset for diversity across runs
        if len(strategies) > 6:
            sample_size = random.randint(4, 6)
            strategies = random.sample(strategies, sample_size)
            print(f"  Sampled {sample_size}/{len(db.list_strategies(active_only=True))} strategies for this run:")
            for s in strategies:
                print(f"    - {s['name']}")
        else:
            print(f"  Using all {len(strategies)} strategies (<=6, no sampling)")

        skill = StrategyGenerationSkill(db=db, backend=backend)
        print(f"  Skill version: v{skill.version}")
        print(f"  Active strategies: {len(strategies)}")

        # Show each strategy
        print_section("Available Strategies (input to LLM)")
        for i, s in enumerate(strategies, 1):
            name = s["name"]
            desc = s["description"]
            channels = s.get("youtube_channels", "[]")
            categories = s.get("youtube_categories", "[]")
            search_tips = s.get("search_tips", "")
            bili_check = s.get("bilibili_check", "")

            try:
                ch_list = json.loads(channels) if channels else []
            except (json.JSONDecodeError, TypeError):
                ch_list = []
            try:
                cat_list = json.loads(categories) if categories else []
            except (json.JSONDecodeError, TypeError):
                cat_list = []

            cat_names = [YT_CATEGORIES.get(c, str(c)) for c in cat_list]

            print(f"\n  {i}. {name}")
            print(f"     Description  : {desc[:90]}")
            if ch_list:
                print(f"     YT Channels  : {', '.join(ch_list)}")
            if cat_names:
                print(f"     YT Categories: {', '.join(cat_names)}")
            if search_tips:
                print(f"     Search tips  : {search_tips[:80]}")
            if bili_check:
                print(f"     Bili check   : {bili_check}")

        # Fetch real Bilibili trending keywords
        print_section("Fetching Bilibili Trending Keywords")
        try:
            trending = await fetch_trending_keywords()
            hot_words = [t.keyword for t in trending]
            print(f"  Found {len(trending)} trending keywords:\n")
            for t in trending[:15]:
                heat_bar = "█" * min(20, t.heat_score // 50000) + "░" * max(0, 20 - t.heat_score // 50000)
                print(f"    #{t.position:<3} {t.keyword:<20} {heat_bar} {t.heat_score:>10,}")
            if len(trending) > 15:
                print(f"    ... and {len(trending) - 15} more")
        except Exception as e:
            print(f"  WARNING: Failed to fetch trending keywords: {e}")
            print(f"  Continuing with empty trending list...")
            hot_words = []
            trending = []

        # Execute strategy generation (1 LLM call)
        print_section("Calling Strategy Generation Skill (LLM)")
        try:
            context = {
                "strategies_with_full_context": skill.format_strategies_context(strategies),
                "recent_outcomes_with_youtube_context": skill.format_recent_outcomes([]),
                "hot_words": skill.format_hot_words(hot_words[:10]),
            }
            gen_output = skill.execute(context)
            llm_call_count += 1
            queries = gen_output.get("queries", [])[:args.max_queries]
            proposals = gen_output.get("new_strategy_proposals", [])
        except Exception as e:
            print(f"  ERROR: Strategy generation failed: {e}")
            print(f"  Cannot continue without queries. Exiting.")
            return

        # Validate strategy_name — LLM may invent non-existent strategies
        known_strategies = {s["name"] for s in db.list_strategies(active_only=True)}
        for q in queries:
            if q["strategy_name"] not in known_strategies:
                old_name = q["strategy_name"]
                # Find a matching strategy by checking if any known name is a substring
                fallback = next(
                    (ks for ks in known_strategies if ks in old_name or old_name in ks),
                    next(iter(known_strategies)),  # absolute fallback: first known strategy
                )
                q["strategy_name"] = fallback
                print(f"  WARNING: LLM invented strategy '{old_name}', mapped to '{fallback}'")

        # Show generated queries
        print_section(f"LLM Generated {len(queries)} Search Queries")
        for i, q in enumerate(queries, 1):
            print(f"\n  Query {i}:")
            print(f"    YouTube search : \"{q['query']}\"")
            print(f"    Strategy       : {q['strategy_name']}")
            print(f"    Bilibili check : {q.get('bilibili_check', 'N/A')}")
            if q.get("target_channels"):
                print(f"    Target channels: {', '.join(q['target_channels'])}")
            print(f"    LLM reasoning  : {q.get('reasoning', 'N/A')}")

        if proposals:
            print_section(f"LLM Proposed {len(proposals)} New Strategies")
            for p in proposals:
                print(f"\n  Name        : {p['name']}")
                print(f"  Description : {p['description']}")
                if p.get("example_queries"):
                    print(f"  Queries     : {', '.join(p['example_queries'][:3])}")
                if p.get("target_channels"):
                    print(f"  Channels    : {', '.join(p['target_channels'])}")
                print(f"  Bili check  : {p.get('bilibili_check', 'N/A')}")
                print(f"  Reasoning   : {p.get('reasoning', 'N/A')}")

        # ================================================================
        # PHASE B: Market Analysis (Bilibili API + LLM per query)
        # ================================================================
        print_header("PHASE B: Bilibili Market Analysis (Real API + LLM)")
        market_skill = MarketAnalysisSkill(db=db, backend=backend)
        print(f"  Skill version: v{market_skill.version}")
        print(f"  Checking each query for Bilibili saturation...\n")

        validated_queries = []
        for i, q in enumerate(queries, 1):
            bilibili_check = q.get("bilibili_check", "")
            if not bilibili_check:
                q["_opportunity_score"] = 0.5
                validated_queries.append(q)
                print(f"  ? Query {i}: no bilibili_check — assuming OPEN (0.50)\n")
                continue

            # Real Bilibili search
            print(f"  Searching Bilibili for: \"{bilibili_check}\"")
            try:
                bili_results = await search_bilibili_similar(bilibili_check, max_results=10)
            except Exception as e:
                print(f"    WARNING: Bilibili search failed: {e}")
                print(f"    Treating as OPEN (opportunity=0.50)")
                q["_opportunity_score"] = 0.5
                validated_queries.append(q)
                print()
                continue

            # Print Bilibili search results
            if bili_results:
                print(f"    Found {len(bili_results)} existing Bilibili videos:\n")
                for j, br in enumerate(bili_results[:5], 1):
                    print(f"      {j}. \"{br.title[:60]}\"")
                    print(f"         Author: {br.author} | Views: {br.views:,} | "
                          f"Danmaku: {br.danmaku:,} | Duration: {fmt_duration(br.duration_seconds)}")
                if len(bili_results) > 5:
                    print(f"      ... and {len(bili_results) - 5} more")
                print()

                # Build market context from real results
                high_view_threshold = 10_000
                high_view_count = sum(1 for r in bili_results if r.views >= high_view_threshold)
                recent_count = len(bili_results)  # all from search are "recent"
                min_views = min(r.views for r in bili_results) if bili_results else 0
                max_views = max(r.views for r in bili_results) if bili_results else 0

                top_videos_str = "\n".join(
                    f"  - \"{r.title[:50]}\" by {r.author} "
                    f"({r.views:,} views, {r.danmaku} danmaku, {fmt_duration(r.duration_seconds)})"
                    for r in sorted(bili_results, key=lambda x: x.views, reverse=True)[:5]
                )
            else:
                print(f"    No existing Bilibili videos found — wide open!\n")
                high_view_count = 0
                recent_count = 0
                min_views = 0
                max_views = 0
                top_videos_str = "(no existing videos found)"

            # Call market analysis LLM
            try:
                market_context = {
                    "bilibili_check": bilibili_check,
                    "total": len(bili_results),
                    "high_view_count": high_view_count,
                    "recent_count": recent_count,
                    "min_views": min_views,
                    "max_views": max_views,
                    "top_videos_with_dates": top_videos_str,
                }
                assessment = market_skill.execute(market_context)
                llm_call_count += 1
            except Exception as e:
                print(f"    WARNING: Market analysis LLM failed: {e}")
                print(f"    Treating as OPEN (opportunity=0.50)")
                q["_opportunity_score"] = 0.5
                validated_queries.append(q)
                print()
                continue

            SATURATION_PENALTY = 0.4  # saturated queries get opportunity * 0.4

            is_sat = assessment.get("is_saturated", False)
            opp = assessment.get("opportunity_score", 0.5)
            status = "SATURATED" if is_sat else "OPEN"
            icon = "▼" if is_sat else "✓"

            print(f"  {icon} Query {i}: \"{bilibili_check}\"")
            print(f"    Status          : {status}")
            print(f"    Opportunity     : {opp:.2f} / 1.00")
            print(f"    Quality gap     : {assessment.get('quality_gap', '?')}")
            print(f"    Freshness gap   : {assessment.get('freshness_gap', '?')}")
            print(f"    Reasoning       : {assessment.get('reasoning', '')[:200]}")
            if assessment.get("suggested_angle"):
                print(f"    Suggested angle : {assessment['suggested_angle'][:120]}")
            print()

            q["_assessment"] = assessment
            if is_sat:
                q["_opportunity_score"] = opp * SATURATION_PENALTY
            else:
                q["_opportunity_score"] = opp
            validated_queries.append(q)  # always keep

        sat_count = sum(1 for q in validated_queries if q.get("_assessment", {}).get("is_saturated"))
        print(f"  Result: {len(validated_queries)} queries ({sat_count} saturated, penalized)")

        # ================================================================
        # PHASE C: YouTube Search + Scoring
        # ================================================================
        print_header("PHASE C: YouTube Video Search + Scoring (Real API)")
        aggregator = SearchAggregator()
        tracker = OutcomeTracker(db)

        # Cross-run dedup: gather already-uploaded video IDs
        already_uploaded: set[str] = set()
        try:
            already_uploaded |= db.get_already_transported_yt_ids()
            print(f"  Already transported (DB): {len(already_uploaded)} video IDs")
        except Exception as e:
            print(f"  WARNING: Could not load transported IDs from DB: {e}")
        if args.upload or args.dry_run:
            try:
                upload_client = UploadClient(args.go_url)
                go_ids = upload_client.get_uploaded_ids()
                already_uploaded |= go_ids
                print(f"  Already uploaded (Go):   {len(go_ids)} video IDs")
            except Exception as e:
                print(f"  WARNING: Could not load uploaded IDs from Go: {e}")
        print(f"  Total dedup set:         {len(already_uploaded)} video IDs")

        print_section("YouTube Search Results (live)")
        for q in validated_queries:
            query_text = q["query"]
            strategy_name = q["strategy_name"]
            opp = q.get("_opportunity_score", 0.5)

            # Look up strategy ID
            strat = db.get_strategy(strategy_name)
            strategy_id = strat["id"] if strat else 1

            # Save strategy run
            run_id = db.save_strategy_run(strategy_id, query_text,
                                          q.get("bilibili_check"))

            # Real YouTube search
            print(f"\n  Query: \"{query_text}\"")
            print(f"  Strategy: {strategy_name} | Opportunity: {opp:.2f}")
            print(f"  Searching YouTube (max_results=10, max_age_days={args.max_age_days})...")

            try:
                results = search_youtube_videos(
                    query_text,
                    max_results=10,
                    max_age_days=args.max_age_days,
                )
            except Exception as e:
                err_msg = str(e)
                if "403" in err_msg or "quota" in err_msg.lower():
                    print(f"  WARNING: YouTube API quota exceeded! Skipping remaining queries.")
                    tracker.record_query_yield(run_id, 0, 0, None)
                    break
                print(f"  WARNING: YouTube search failed: {e}")
                results = []

            if results:
                best = max(results, key=lambda r: r.views)
                avg_views = sum(r.views for r in results) // len(results)

                # Record yield
                best_dict = {
                    "id": best.video_id,
                    "title": best.title,
                    "channel": best.channel_title,
                    "views": best.views,
                    "likes": best.likes,
                    "category_id": best.category_id,
                    "duration_seconds": best.duration_seconds,
                }
                tracker.record_query_yield(run_id, len(results), avg_views, best_dict)

                print(f"  Found {len(results)} video(s):\n")
                for j, r in enumerate(results, 1):
                    like_ratio = r.likes / max(r.views, 1)
                    cat_name = YT_CATEGORIES.get(r.category_id, str(r.category_id))
                    print(f"    {j}. \"{r.title}\"")
                    print(f"       Channel  : {r.channel_title}")
                    print(f"       Views    : {r.views:>12,}")
                    print(f"       Likes    : {r.likes:>12,}  ({like_ratio:.1%} like ratio)")
                    print(f"       Duration : {fmt_duration(r.duration_seconds)} ({r.duration_seconds}s)")
                    print(f"       Category : {cat_name} (id={r.category_id})")
                    print(f"       Published: {r.published_at[:10] if r.published_at else 'N/A'}")
                    print()

                # Add to aggregator (skip already-uploaded)
                for r in results:
                    if r.video_id in already_uploaded:
                        print(f"       SKIP (already uploaded): {r.video_id}")
                        continue
                    aggregator.add(
                        video_id=r.video_id,
                        title=r.title,
                        channel=r.channel_title,
                        views=r.views,
                        likes=r.likes,
                        duration_seconds=r.duration_seconds,
                        category_id=r.category_id,
                        opportunity_score=opp,
                        strategy=strategy_name,
                        query=query_text,
                    )
            else:
                tracker.record_query_yield(run_id, 0, 0, None)
                print(f"  Found 0 videos.\n")

            # Update strategy yield stats
            tracker.update_strategy_yield_stats(strategy_id)

        # ── Heuristic Scoring ──
        print_section("Heuristic Scoring (all candidates)")
        candidates = aggregator.get_candidates(min_views=params.youtube_min_views)

        print(f"\n  Total unique candidates : {aggregator.count()}")
        print(f"  Above {params.youtube_min_views:,} views    : {len(candidates)}")

        if not candidates:
            print(f"\n  No candidates above the minimum view threshold.")
            print(f"  Try lowering --max-age-days or adjusting strategies.")

            # Still print summary
            print_header("FINAL SUMMARY")
            elapsed = time.time() - t_start
            print(f"""
  Pipeline Statistics
  ───────────────────
  LLM calls made         : {llm_call_count}
  Queries generated      : {len(queries)}
  Passed saturation      : {len(validated_queries)}/{len(queries)}
  YouTube videos found   : {aggregator.count()}
  Above min-view filter  : 0/{aggregator.count()}
  Elapsed time           : {elapsed:.1f}s
""")
            return

        print(f"\n  Scoring formula:")
        print(f"    score = (engagement × {params.engagement_weight} + view_signal × {params.view_signal_weight}"
              f" + opportunity × {params.opportunity_weight} + duration × {params.duration_weight}) × category_bonus")

        scored = []
        for c in candidates:
            score = heuristic_score(
                c.views, c.likes, c.duration_seconds, c.category_id,
                c.opportunity_score, params,
            )
            scored.append((c, score))

        scored.sort(key=lambda x: x[1], reverse=True)

        print(f"\n  Ranked Results:")
        print(f"  {'#':<3} {'Score':<8} {'Title':<45} {'Channel':<16} {'Views':>10} {'Like%':>6} {'Dur':>7} {'Opp':>5}")
        print(f"  {'─'*3} {'─'*8} {'─'*45} {'─'*16} {'─'*10} {'─'*6} {'─'*7} {'─'*5}")

        for rank, (c, score) in enumerate(scored, 1):
            like_ratio = c.likes / max(c.views, 1)
            print(f"  {rank:<3} {score:<8.4f} {c.title[:45]:<45} {c.channel[:16]:<16} "
                  f"{c.views:>10,} {like_ratio:>5.1%} {fmt_duration(c.duration_seconds):>7} "
                  f"{c.opportunity_score:>5.2f}")

        # Diverse top-N: pick best from each strategy first, then fill
        top_n = min(args.top_n, len(scored))
        diverse_top = []
        seen_strategies: set[str] = set()
        # Pass 1: best per strategy
        for c, score in scored:
            primary = c.source_strategies[0] if c.source_strategies else ""
            if primary not in seen_strategies:
                diverse_top.append((c, score))
                seen_strategies.add(primary)
            if len(diverse_top) >= top_n:
                break
        # Pass 2: fill remaining slots by score (if fewer strategies than top_n)
        if len(diverse_top) < top_n:
            already = {id(c) for c, _ in diverse_top}
            for c, score in scored:
                if id(c) not in already:
                    diverse_top.append((c, score))
                if len(diverse_top) >= top_n:
                    break
        scored_top = diverse_top[:top_n]

        print_section(f"Detailed Scoring Breakdown (top {top_n}, strategy-diverse)")
        for rank, (c, score) in enumerate(scored_top, 1):
            like_ratio = c.likes / max(c.views, 1)
            engagement = min(1.0, like_ratio / params.engagement_good_threshold)
            view_signal = min(1.0, math.log1p(c.views) / math.log1p(1_000_000))
            lo, hi = params.duration_sweet_spot
            dur_score = 1.0 if lo <= c.duration_seconds <= hi else 0.7
            cat_bonus = params.category_bonuses.get(c.category_id, 1.0)
            cat_name = YT_CATEGORIES.get(c.category_id, str(c.category_id))

            raw = (engagement * params.engagement_weight
                   + view_signal * params.view_signal_weight
                   + c.opportunity_score * params.opportunity_weight
                   + dur_score * params.duration_weight)

            in_sweet = "YES" if lo <= c.duration_seconds <= hi else f"NO (outside {fmt_duration(lo)}–{fmt_duration(hi)})"

            print(f"\n  #{rank} \"{c.title}\"")
            print(f"    Channel        : {c.channel}")
            print(f"    Strategy       : {', '.join(c.source_strategies)}")
            print(f"    ┌─ Engagement  : like_ratio={like_ratio:.4f} / threshold={params.engagement_good_threshold} = {engagement:.4f} × {params.engagement_weight} = {engagement * params.engagement_weight:.4f}")
            print(f"    │  View signal : log1p({c.views:,}) / log1p(1,000,000) = {view_signal:.4f} × {params.view_signal_weight} = {view_signal * params.view_signal_weight:.4f}")
            print(f"    │  Opportunity : {c.opportunity_score:.2f} × {params.opportunity_weight} = {c.opportunity_score * params.opportunity_weight:.4f}")
            print(f"    │  Duration    : {fmt_duration(c.duration_seconds)} in sweet spot? {in_sweet} = {dur_score:.1f} × {params.duration_weight} = {dur_score * params.duration_weight:.4f}")
            print(f"    │  Raw total   : {raw:.4f}")
            print(f"    │  Cat bonus   : {cat_name} (id={c.category_id}) = {cat_bonus:.2f}x")
            print(f"    └─ Final score : {raw:.4f} × {cat_bonus:.2f} = {score:.4f}")

        # ── Transportability Check (LLM, top N) ──
        print_section(f"Transportability Check (LLM, top {top_n})")
        transport_results = []
        for rank, (c, score) in enumerate(scored_top, 1):
            try:
                result = check_transportability(
                    backend, c.title, c.channel, c.duration_seconds, c.category_id,
                )
                llm_call_count += 1
            except Exception as e:
                print(f"\n  #{rank} \"{c.title[:60]}\"")
                print(f"    ERROR: Transportability check failed: {e}")
                result = {"transportable": True, "reasoning": f"Check failed: {e}"}

            status = "PASS" if result["transportable"] else "FAIL"
            persona_fit = result.get("persona_fit", 0.5)
            print(f"\n  #{rank} \"{c.title[:60]}\"")
            print(f"    Verdict      : {status}")
            print(f"    Persona fit  : {persona_fit:.1f}")
            print(f"    Reasoning    : {result['reasoning'][:200]}")
            transport_results.append((c, score, result))

        # ================================================================
        # PHASE D: Yield Reflection (Loop 1) — unless --skip-reflection
        # ================================================================
        if not args.skip_reflection:
            print_header("PHASE D: Yield Reflection (Loop 1 — fast feedback)")

            yield_data = db.get_latest_run_yields(limit=10)
            strategy_stats = db.get_strategy_yield_stats()

            print(f"  Runs recorded this session: {len(yield_data)}")
            print(f"\n  Strategy yield rates:")
            for s in strategy_stats:
                if s["total_queries"] > 0:
                    bar_len = int(s["yield_rate"] * 20)
                    bar = "█" * bar_len + "░" * (20 - bar_len)
                    print(f"    {s['name']:<35} {bar} {s['yield_rate']:>5.0%}  "
                          f"({s['yielded_queries']}/{s['total_queries']})")

            # Run yield reflection
            print_section("LLM Yield Reflection")
            try:
                reflection = skill.reflect_on_yield(yield_data, strategy_stats)
                llm_call_count += 1

                if reflection:
                    print(f"\n  Analysis:")
                    wrap_print(reflection.get("analysis", ""))

                    yt_updated = reflection.get("updated_youtube_principles")
                    print(f"\n  YouTube principles updated: "
                          f"{'YES — new version saved' if yt_updated else 'no change'}")
                    if yt_updated:
                        if isinstance(yt_updated, list):
                            yt_updated = "\n".join(str(p) for p in yt_updated)
                        print(f"\n  New YouTube principles:")
                        for line in yt_updated.strip().split("\n"):
                            print(f"    {line}")

                    channels = reflection.get("channels_to_follow", [])
                    if channels:
                        print(f"\n  New channels to follow ({len(channels)}):")
                        for ch in channels:
                            print(f"    {ch.get('channel_name', '?')}: "
                                  f"{ch.get('reason', '')}")
            except Exception as e:
                print(f"  ERROR: Yield reflection failed: {e}")

            skill_row = db.get_skill("strategy_generation")
            if skill_row:
                print(f"\n  Skill version: v{skill.version} -> v{skill_row['version']}")
        else:
            print(f"\n  (Skipping reflection — --skip-reflection flag set)")

        # ================================================================
        # PHASE E: Upload Top Candidates
        # ================================================================
        upload_results = []
        if args.upload or args.dry_run:
            mode = "DRY RUN" if args.dry_run else "Upload"
            print_header(f"PHASE E: {mode} — Top Candidates to Bilibili")
            if not args.dry_run:
                upload_client = UploadClient(args.go_url)
                print(f"  Go service URL : {args.go_url}")
            else:
                print(f"  Mode           : DRY RUN (no HTTP requests)")
            print(f"  Candidates     : {len(transport_results)}")

            # Prepare all upload payloads
            upload_payloads = []
            for rank, (c, score, tr) in enumerate(transport_results, 1):
                if not tr["transportable"]:
                    print(f"\n  #{rank} SKIP — failed transportability check")
                    continue

                print(f"\n  #{rank} \"{c.title[:60]}\"")

                # Generate persona-driven title + description (single LLM call)
                try:
                    video_info = {
                        "title": c.title,
                        "view_count": c.views,
                        "channel": c.channel,
                        "video_id": c.video_id,
                        "duration_seconds": c.duration_seconds,
                        "category_id": c.category_id,
                    }
                    strategy = c.source_strategies[0] if c.source_strategies else ""
                    copy = generate_persona_copy(backend, video_info, strategy_name=strategy)
                    chinese_title = copy["title"]
                    desc = copy["description"]
                    llm_call_count += 1
                    print(f"    Persona title: {chinese_title}")
                    print(f"    Persona desc:  {desc[:80]}...")
                except Exception as e:
                    print(f"    WARNING: Persona copy failed: {e}")
                    chinese_title = c.title
                    desc = f"本视频搬运自YouTube\n原视频链接：https://www.youtube.com/watch?v={c.video_id}"

                # Generate tags from similar Bilibili videos
                try:
                    tag_list = await generate_tags_from_similar(
                        chinese_title, max_similar=10, max_tags=10,
                    )
                    if tag_list:
                        print(f"    Tags from similar videos ({len(tag_list)}): {', '.join(tag_list)}")
                    else:
                        print(f"    No tags found from similar videos")
                except Exception as e:
                    print(f"    WARNING: Tag generation failed: {e}")
                    tag_list = []
                tags = ",".join(tag_list)

                # Build request payload
                request_payload = {
                    "video_id": c.video_id,
                    "title": chinese_title,
                    "description": desc,
                    "tags": tags,
                }

                # Print exact HTTP request payload
                payload_json = json.dumps(request_payload, ensure_ascii=False, indent=2)
                print(f"    ┌─ POST {args.go_url}/upload")
                print(f"    │  Content-Type: application/json")
                print(f"    │")
                for line in payload_json.split("\n"):
                    print(f"    │  {line}")

                if args.dry_run:
                    print(f"    └─ (dry run — skipped)")
                    continue

                upload_payloads.append((c, score, request_payload))

            # Submit all jobs at once (non-blocking), then poll
            if not args.dry_run and upload_payloads:
                print_section("Submitting all upload jobs")
                submitted_jobs = []
                for c, score, payload in upload_payloads:
                    result = upload_client.submit_upload(
                        video_id=payload["video_id"],
                        title=payload["title"],
                        description=payload["description"],
                        tags=payload["tags"],
                    )
                    job_id = result.get("job_id")
                    if job_id:
                        print(f"  Submitted job {job_id} for {payload['video_id']}")
                    else:
                        print(f"  FAILED to submit {payload['video_id']}: {result.get('error', 'unknown')}")
                    submitted_jobs.append((c, score, result))

                # Poll all jobs until complete
                print_section("Polling job status (processing serially on server)")
                poll_deadline = time.time() + 1800  # 30 min
                while time.time() < poll_deadline:
                    all_done = True
                    for i, (c, score, sub) in enumerate(submitted_jobs):
                        job_id = sub.get("job_id")
                        if not job_id or sub.get("status") in ("completed", "failed"):
                            continue
                        status_resp = upload_client.get_status(job_id)
                        current_status = status_resp.get("status", "unknown")
                        if current_status in ("completed", "failed"):
                            submitted_jobs[i] = (c, score, status_resp)
                            bvid = status_resp.get("bilibili_bvid", "")
                            err = status_resp.get("error_message", "")
                            if current_status == "completed" and bvid:
                                print(f"  Job {job_id}: SUCCESS — bvid: {bvid}")
                            elif err:
                                print(f"  Job {job_id}: FAILED — {err}")
                            else:
                                print(f"  Job {job_id}: {current_status}")
                        else:
                            all_done = False
                    if all_done:
                        break
                    time.sleep(10)

                upload_results = [(c, score, r) for c, score, r in submitted_jobs]
        else:
            print(f"\n  (Upload phase disabled — use --upload or --dry-run to enable)")

        # ================================================================
        # PHASE F: Summary
        # ================================================================
        print_header("FINAL SUMMARY")

        elapsed = time.time() - t_start
        final_strategies = db.list_strategies(active_only=True)
        final_channels = db.list_followed_channels()
        strategy_skill_row = db.get_skill("strategy_generation")
        market_skill_row = db.get_skill("market_analysis")

        print(f"""
  Pipeline Statistics
  ───────────────────
  LLM calls made         : {llm_call_count}
  Queries generated      : {len(queries)}
  Passed saturation      : {len(validated_queries)}/{len(queries)}
  YouTube videos found   : {aggregator.count()}
  Above min-view filter  : {len(candidates)}/{aggregator.count()}
  Elapsed time           : {elapsed:.1f}s

  System State
  ───────────────────
  Active strategies      : {len(final_strategies)}
  Followed channels      : {len(final_channels)}""")
        if strategy_skill_row:
            print(f"  Strategy skill version : v{strategy_skill_row['version']}")
        if market_skill_row:
            print(f"  Market skill version   : v{market_skill_row['version']}")

        # Recommended videos to transport
        print_section(f"Recommended Videos to Transport (top {top_n})")
        for rank, (c, score, tr) in enumerate(transport_results, 1):
            like_ratio = c.likes / max(c.views, 1)
            transport_icon = "✓" if tr["transportable"] else "✗"
            print(f"\n  {transport_icon} #{rank} (score={score:.4f})")
            print(f"    Title      : {c.title}")
            print(f"    Channel    : {c.channel}")
            print(f"    Views      : {c.views:,}")
            print(f"    Like ratio : {like_ratio:.1%}")
            print(f"    Duration   : {fmt_duration(c.duration_seconds)}")
            print(f"    Category   : {YT_CATEGORIES.get(c.category_id, str(c.category_id))}")
            print(f"    Strategy   : {', '.join(c.source_strategies)}")
            print(f"    Persona fit: {tr.get('persona_fit', 0.5):.1f}")
            print(f"    Transport  : {'PASS' if tr['transportable'] else 'FAIL'} — {tr['reasoning'][:120]}")

        # Upload results
        if upload_results:
            print_section("Upload Results")
            for rank, (c, score, result) in enumerate(upload_results, 1):
                status = result.get("status", "unknown")
                bvid = result.get("bilibili_bvid", "") or result.get("bilibili_bvid", "")
                error = result.get("error", "") or result.get("error_message", "")
                icon = "✓" if status == "completed" else "✗"
                print(f"  {icon} #{rank} \"{c.title[:50]}\"")
                if bvid:
                    print(f"    Bilibili: https://www.bilibili.com/video/{bvid}")
                if error:
                    print(f"    Error: {error}")

        # Strategy yield summary
        print(f"\n  Strategy Yield Summary:")
        for s in db.get_strategy_yield_stats():
            if s["total_queries"] > 0:
                bar_len = int(s["yield_rate"] * 20)
                bar = "█" * bar_len + "░" * (20 - bar_len)
                print(f"    {s['name']:<35} {bar} {s['yield_rate']:>5.0%}")

        # Final principles (if available)
        if hasattr(skill, "youtube_principles") and skill.youtube_principles:
            print_section("Current YouTube Principles")
            yt_p = skill.youtube_principles
            if isinstance(yt_p, list):
                yt_p = "\n".join(str(p) for p in yt_p)
            for line in str(yt_p).strip().split("\n"):
                print(f"  {line}")

        if hasattr(skill, "bilibili_principles") and skill.bilibili_principles:
            print_section("Current Bilibili Principles")
            bl_p = skill.bilibili_principles
            if isinstance(bl_p, list):
                bl_p = "\n".join(str(p) for p in bl_p)
            for line in str(bl_p).strip().split("\n"):
                print(f"  {line}")

    finally:
        db.close()

    print(f"\n{'=' * 76}")
    print(f"  Real discovery run complete! ({elapsed:.1f}s)")
    print(f"{'=' * 76}\n")


if __name__ == "__main__":
    asyncio.run(main())
