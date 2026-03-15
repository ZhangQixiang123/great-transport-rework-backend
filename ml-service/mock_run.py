#!/usr/bin/env python3
"""Mock end-to-end discovery run using the skill-based framework.

Simulates: bootstrap -> strategy generation -> market analysis ->
search -> scoring -> outcome tracking -> reflection

Run:  cd ml-service && .venv/Scripts/python mock_run.py
"""
import json
import math
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app.db.database import Database
from app.bootstrap import run_bootstrap
from app.skills.strategy_generation import StrategyGenerationSkill
from app.skills.market_analysis import MarketAnalysisSkill
from app.scoring.heuristic import ScoringParams, heuristic_score
from app.scoring.transportability import check_transportability
from app.search.aggregator import SearchAggregator
from app.outcomes.tracker import OutcomeTracker


# ── YouTube category ID -> name mapping ──────────────────────────────

YT_CATEGORIES = {
    1: "Film & Animation", 2: "Autos & Vehicles", 10: "Music",
    15: "Pets & Animals", 17: "Sports", 19: "Travel & Events",
    20: "Gaming", 22: "People & Blogs", 23: "Comedy",
    24: "Entertainment", 25: "News & Politics", 26: "Howto & Style",
    27: "Education", 28: "Science & Technology",
}


class MockBackend:
    """Simulates LLM responses for each skill call."""

    def __init__(self):
        self._call_count = 0
        self._call_log = []

    def chat(self, messages, json_schema=None):
        self._call_count += 1
        user_msg = messages[-1]["content"] if messages else ""

        # Detect which skill is calling based on content
        if "Find YouTube videos suitable for transport" in user_msg:
            label = "StrategyGeneration.execute"
            resp = self._strategy_generation_response()
        elif "Analyze the Bilibili market" in user_msg:
            label = "MarketAnalysis.execute"
            resp = self._market_analysis_response(user_msg)
        elif "YouTube search performance" in user_msg:
            label = "StrategyGeneration.reflect_on_yield (Loop 1)"
            resp = self._yield_reflection_response()
        elif "Bilibili performance of videos" in user_msg:
            label = "StrategyGeneration.reflect_on_outcomes (Loop 2)"
            resp = self._outcome_reflection_response()
        elif "market saturation judgments" in user_msg:
            label = "MarketAnalysis.reflect_on_outcomes (Loop 2)"
            resp = self._market_reflection_response()
        elif "suitable for transport to Bilibili" in user_msg:
            label = "check_transportability"
            resp = self._transportability_response()
        elif "Analyze this historical" in user_msg:
            label = "bootstrap_principles"
            resp = self._bootstrap_principles_response()
        else:
            label = "unknown"
            resp = '{"status": "ok"}'

        self._call_log.append(label)
        return resp

    def _strategy_generation_response(self):
        return json.dumps({
            "queries": [
                {
                    "query": "foreigner tries Sichuan mapo tofu authentic",
                    "strategy_name": "foreign_appreciation",
                    "bilibili_check": "外国人 麻婆豆腐",
                    "target_channels": ["@thefoodranger", "@mikechen"],
                    "reasoning": "Sichuan food content consistently performs well; specific dish focus beats generic 'Chinese food'"
                },
                {
                    "query": "BYD Seal honest long term review 2026",
                    "strategy_name": "chinese_brand_foreign_review",
                    "bilibili_check": "BYD 海豹 外国评测",
                    "target_channels": ["@carwow", "@fullychargedshow"],
                    "reasoning": "BYD Seal is gaining international attention; long-term reviews are rare and valuable"
                },
                {
                    "query": "how semiconductor chips are manufactured factory tour",
                    "strategy_name": "behind_the_scenes",
                    "bilibili_check": "芯片 制造过程",
                    "target_channels": ["@branchEducation"],
                    "reasoning": "Chip manufacturing is trending; factory tours are visually compelling"
                },
                {
                    "query": "Mark Rober impossible basketball trick shot",
                    "strategy_name": "challenge_experiment",
                    "bilibili_check": "马克罗伯 挑战",
                    "target_channels": ["@markrober"],
                    "reasoning": "Mark Rober's engineering challenges have high cross-cultural appeal"
                },
                {
                    "query": "Kurzgesagt immune system explained animation",
                    "strategy_name": "educational_explainer",
                    "bilibili_check": "免疫系统 科普",
                    "target_channels": ["@kurzgesagt"],
                    "reasoning": "Kurzgesagt animations are language-independent and consistently popular"
                },
            ],
            "new_strategy_proposals": [
                {
                    "name": "ai_tools_practical",
                    "description": "Practical AI tool tutorials and demonstrations that show real productivity gains",
                    "youtube_tactics": "Search for 'AI tool tutorial', 'ChatGPT workflow', filter by recent uploads",
                    "example_queries": ["AI tools that actually save time 2026", "ChatGPT advanced workflow tutorial"],
                    "target_channels": ["@mattvidpro", "@allaboutai"],
                    "bilibili_check": "AI工具 教程",
                    "reasoning": "AI tools content is trending globally; practical tutorials resonate with Bilibili's tech audience"
                }
            ],
            "retire_suggestions": []
        })

    def _market_analysis_response(self, prompt):
        if "麻婆豆腐" in prompt:
            return json.dumps({
                "is_saturated": False,
                "opportunity_score": 0.75,
                "quality_gap": "high",
                "freshness_gap": "medium",
                "reasoning": "Only 2 recent transports of foreigner + mapo tofu content. Existing videos are older (3+ months) and lower production quality. There's a clear gap for a well-produced video featuring a foreigner's authentic first reaction to real Sichuan mapo tofu, not the tourist-friendly version.",
                "suggested_angle": "Focus on authentic Sichuan preparation, not tourist-friendly version",
                "existing_top_videos": "1. '外国小哥试吃麻婆豆腐' (45K views, 4 months ago) 2. '美国人第一次吃正宗麻婆豆腐' (28K views, 6 months ago)"
            })
        elif "BYD" in prompt:
            return json.dumps({
                "is_saturated": False,
                "opportunity_score": 0.85,
                "quality_gap": "medium",
                "freshness_gap": "high",
                "reasoning": "BYD Seal reviews are hot on Bilibili but 90% are short-form (<3 min) first impression videos. Nobody has transported a long-term (6+ month) foreign ownership review yet. The audience craves honest long-term feedback from international drivers.",
                "suggested_angle": "Emphasize long-term ownership experience (6+ months), not just first impressions",
                "existing_top_videos": "1. '比亚迪海豹海外评测合集' (120K views, 2 months ago) 2. 'BYD Seal德国车评人测评' (85K views, 1 month ago)"
            })
        elif "芯片" in prompt:
            return json.dumps({
                "is_saturated": True,
                "opportunity_score": 0.2,
                "quality_gap": "low",
                "freshness_gap": "low",
                "reasoning": "Chip manufacturing topic is heavily covered on Bilibili already. Found 12 videos with >100K views in the last 30 days alone. Multiple established UP主 (uploaders) already dominate this niche with high-quality factory tour content. New entrant would struggle to compete.",
                "suggested_angle": "N/A - topic is saturated, skip this query",
                "existing_top_videos": "1. '走进台积电工厂' (890K views, 2 weeks ago) 2. '芯片制造全过程4K' (560K views, 3 weeks ago) 3. '英伟达芯片工厂探秘' (340K views, 1 month ago)"
            })
        elif "马克罗伯" in prompt:
            return json.dumps({
                "is_saturated": False,
                "opportunity_score": 0.7,
                "quality_gap": "medium",
                "freshness_gap": "medium",
                "reasoning": "Mark Rober videos get transported regularly but there's always demand for his latest content. His basketball trick shot video hasn't appeared on Bilibili yet. Speed of transport is the key differentiator — first mover gets 80% of the views.",
                "suggested_angle": "Speed matters - transport within 48h of YouTube upload for maximum impact",
                "existing_top_videos": "1. '马克罗伯 世界最大水枪' (280K views, 2 months ago) 2. '马克罗伯 松鼠障碍赛' (195K views, 4 months ago)"
            })
        else:
            return json.dumps({
                "is_saturated": False,
                "opportunity_score": 0.6,
                "quality_gap": "medium",
                "freshness_gap": "medium",
                "reasoning": "Moderate opportunity in this niche. Some existing content but room for quality differentiation.",
                "suggested_angle": "Standard approach — focus on production quality",
                "existing_top_videos": "(simulated)"
            })

    def _yield_reflection_response(self):
        return json.dumps({
            "updated_youtube_principles": (
                "- Specific dish/product names outperform generic categories "
                "(\"Sichuan mapo tofu\" > \"Chinese food\", 3x higher video quality)\n"
                "- Adding \"honest\" or \"long term\" to review queries finds higher quality content\n"
                "- Mark Rober and Kurzgesagt are reliable sources — check their channels directly\n"
                "- Factory tour queries have high yield but topics saturate fast on Bilibili\n"
                "- Filtering by <3 months upload date avoids stale content\n"
                "- Queries returning videos >200K views have 2.5x higher transport success"
            ),
            "new_strategies": [],
            "channels_to_follow": [
                {"channel_id": "UC_x5XG1OV2P6uZZ5FSM9Ttw", "channel_name": "@GoogleDeepMind", "reason": "AI research visualizations have cross-cultural appeal"}
            ],
            "retire": [],
            "analysis": (
                "4 out of 5 queries yielded good YouTube results. The chip manufacturing query "
                "('how semiconductor chips are manufactured factory tour') was correctly filtered "
                "by market analysis as saturated (opportunity=0.20), saving us a wasted transport. "
                "BYD long-term review query found an exceptionally high-quality video (890K views, "
                "5.8% like ratio). Food queries with specific dish names continue to outperform "
                "generic food queries. Mark Rober and Kurzgesagt channels are reliable — consider "
                "adding direct channel monitoring."
            )
        })

    def _outcome_reflection_response(self):
        return json.dumps({
            "updated_bilibili_principles": (
                "- Food content with specific dishes outperforms generic cuisine videos (avg 180K vs 45K views)\n"
                "- Chinese brand reviews by respected foreign creators get highest views (avg 220K)\n"
                "- Videos under 12 minutes transport better than longer ones on Bilibili\n"
                "- Educational content from top channels (Kurzgesagt, Veritasium) reliably gets 80K+ views\n"
                "- Speed of transport matters: videos uploaded within 48h of YouTube release get 3x more views\n"
                "- Avoid purely English-dialogue content without strong visual narrative"
            ),
            "scoring_insights": (
                "YouTube like_ratio > 0.05 correlates strongly with Bilibili success (85% of "
                "successes had >5% like ratio). Duration sweet spot is 5-12 minutes on Bilibili "
                "(shorter than the 5-15 min YouTube sweet spot). Category 2 (Autos) and 22 "
                "(People & Blogs) overperformed this round."
            ),
            "analysis": (
                "Both transported videos succeeded. The BYD review (220K Bilibili views) confirms "
                "that Chinese brand + foreign reviewer is a winning formula — especially long-term "
                "reviews which are rare. The mapo tofu video (185K views) validates that specific "
                "dish names work. Both videos had like_ratio > 5% on YouTube, which we should "
                "adopt as a hard filter."
            )
        })

    def _market_reflection_response(self):
        return json.dumps({
            "updated_criteria": (
                "- >8 videos with >10K views in 30 days = saturated (raised from 5, was too conservative)\n"
                "- Freshness gap is the strongest predictor: no uploads in 60 days = 3x success rate\n"
                "- Quality gap matters for food/culture content but less for tech content\n"
                "- Check danmaku count: >500 danmaku on existing videos = engaged audience, avoid competing\n"
                "- Only 1-2 active transport channels in a niche = best opportunity"
            ),
            "threshold_adjustments": "Raised saturation threshold from 5 to 8 high-view videos. Added danmaku count as a new saturation signal.",
            "analysis": (
                "Our saturation judgments were accurate this round. The chip manufacturing topic "
                "(opportunity=0.20) was correctly flagged as saturated. BYD reviews (0.85) and "
                "mapo tofu (0.75) both succeeded. Our conservative threshold was slightly too "
                "aggressive in past rounds — raising to 8 high-view videos should fix this."
            )
        })

    def _transportability_response(self):
        return json.dumps({
            "transportable": True,
            "reasoning": "Visual content with universal appeal, minimal language dependency. Strong narrative arc that works without subtitles."
        })

    def _bootstrap_principles_response(self):
        return json.dumps({
            "youtube_principles": "- Category 28 (Science & Tech) has highest success rate\n- Videos >200K views transport best",
            "bilibili_principles": "- Food content is king on Bilibili\n- Under 15 minutes is optimal duration"
        })


# ── Simulated YouTube search results ─────────────────────────────────

MOCK_YOUTUBE_RESULTS = {
    "foreigner tries Sichuan mapo tofu authentic": [
        {
            "id": "yt_mapo_01",
            "title": "American Chef Tries REAL Sichuan Mapo Tofu (Mind Blown)",
            "channel": "@thefoodranger",
            "views": 420_000,
            "likes": 28_000,
            "duration_seconds": 680,
            "category_id": 22,
            "upload_date": "2026-02-28",
            "description": "I traveled to Chengdu to try the most authentic mapo tofu...",
        },
        {
            "id": "yt_mapo_02",
            "title": "I Traveled to Chengdu Just for This Dish",
            "channel": "@mikechen",
            "views": 180_000,
            "likes": 12_000,
            "duration_seconds": 540,
            "category_id": 19,
            "upload_date": "2026-03-01",
            "description": "My journey to find the perfect mapo tofu in Sichuan...",
        },
    ],
    "BYD Seal honest long term review 2026": [
        {
            "id": "yt_byd_01",
            "title": "I've Driven a BYD Seal for 6 Months - Here's the TRUTH",
            "channel": "@carwow",
            "views": 890_000,
            "likes": 52_000,
            "duration_seconds": 780,
            "category_id": 2,
            "upload_date": "2026-03-05",
            "description": "After 6 months and 12,000 miles with the BYD Seal...",
        },
    ],
    "how semiconductor chips are manufactured factory tour": [],  # saturated, filtered out
    "Mark Rober impossible basketball trick shot": [
        {
            "id": "yt_rober_01",
            "title": "World's Most Impossible Basketball Shot",
            "channel": "@markrober",
            "views": 15_200_000,
            "likes": 890_000,
            "duration_seconds": 1020,
            "category_id": 28,
            "upload_date": "2026-03-10",
            "description": "I built a robot that can make any basketball shot...",
        },
    ],
    "Kurzgesagt immune system explained animation": [
        {
            "id": "yt_kurz_01",
            "title": "The Immune System Explained - Your Body's Invisible Army",
            "channel": "@kurzgesagt",
            "views": 8_500_000,
            "likes": 420_000,
            "duration_seconds": 480,
            "category_id": 27,
            "upload_date": "2026-02-20",
            "description": "How does your immune system actually work?...",
        },
    ],
}


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


def print_box(lines, indent=4):
    """Print lines inside a light box."""
    prefix = " " * indent
    width = max(len(l) for l in lines) + 2 if lines else 40
    width = min(width, 72)
    print(f"{prefix}┌{'─' * width}┐")
    for l in lines:
        print(f"{prefix}│ {l:<{width - 2}} │")
    print(f"{prefix}└{'─' * width}┘")


def main():
    db = Database("data.db")
    db.connect()
    backend = MockBackend()

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

        # Load scoring params
        params_row = db.get_scoring_params()
        if params_row:
            params = ScoringParams.from_json(params_row["params_json"])
        else:
            params = ScoringParams()

        print(f"\n  Scoring parameters (derived from competitor data):")
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
            for cat_id, bonus in sorted(params.category_bonuses.items(), key=lambda x: x[1], reverse=True)[:8]:
                cat_name = YT_CATEGORIES.get(cat_id, f"Category {cat_id}")
                direction = "+" if bonus >= 1.0 else "-"
                print(f"      {cat_name:<25} {bonus:.2f}x {'▲' if bonus > 1 else '▼' if bonus < 1 else '─'}")

        # ================================================================
        # PHASE A: Strategy Generation
        # ================================================================
        print_header("PHASE A: Strategy Generation (LLM Skill)")

        strategies = db.list_strategies(active_only=True)
        skill = StrategyGenerationSkill(db=db, backend=backend)
        print(f"  Skill version: v{skill.version}")
        print(f"  Active strategies: {len(strategies)}")

        # Show each strategy the LLM has to work with
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

        # Build context and execute
        context = {
            "strategies_with_full_context": skill.format_strategies_context(strategies),
            "recent_outcomes_with_youtube_context": skill.format_recent_outcomes([]),
            "hot_words": skill.format_hot_words(["AI大模型", "新能源汽车", "外国人中国", "芯片", "科技"]),
        }
        gen_output = skill.execute(context)
        queries = gen_output.get("queries", [])
        proposals = gen_output.get("new_strategy_proposals", [])

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
        # PHASE B: Market Analysis (Bilibili saturation check)
        # ================================================================
        print_header("PHASE B: Bilibili Market Analysis (LLM Skill)")
        market_skill = MarketAnalysisSkill(db=db, backend=backend)
        print(f"  Skill version: v{market_skill.version}")
        print(f"  Checking each query for Bilibili saturation...\n")

        validated_queries = []
        for i, q in enumerate(queries, 1):
            bilibili_check = q.get("bilibili_check", "")
            if not bilibili_check:
                validated_queries.append(q)
                continue

            market_context = {
                "bilibili_check": bilibili_check,
                "total": 15,
                "high_view_count": 3,
                "recent_count": 5,
                "min_views": 1000,
                "max_views": 500000,
                "top_videos_with_dates": "(simulated Bilibili search results)",
            }
            assessment = market_skill.execute(market_context)

            is_sat = assessment.get("is_saturated", False)
            opp = assessment.get("opportunity_score", 0)
            status = "SATURATED" if is_sat else "OPEN"
            icon = "✗" if is_sat else "✓"

            print(f"  {icon} Query {i}: \"{bilibili_check}\"")
            print(f"    Status          : {status}")
            print(f"    Opportunity     : {opp:.2f} / 1.00")
            print(f"    Quality gap     : {assessment.get('quality_gap', '?')}")
            print(f"    Freshness gap   : {assessment.get('freshness_gap', '?')}")
            print(f"    Reasoning       : {assessment.get('reasoning', '')}")
            if assessment.get("suggested_angle"):
                print(f"    Suggested angle : {assessment['suggested_angle']}")
            if assessment.get("existing_top_videos"):
                print(f"    Existing videos : {assessment['existing_top_videos']}")
            print()

            if not is_sat:
                q["_opportunity_score"] = opp
                q["_assessment"] = assessment
                validated_queries.append(q)

        print(f"  Result: {len(validated_queries)}/{len(queries)} queries passed saturation check")

        # ================================================================
        # PHASE C: YouTube Video Search + Heuristic Scoring
        # ================================================================
        print_header("PHASE C: YouTube Video Search + Scoring")
        aggregator = SearchAggregator()
        tracker = OutcomeTracker(db)

        print_section("Simulated YouTube Search Results")
        for q in validated_queries:
            query_text = q["query"]
            strategy_name = q["strategy_name"]
            opp = q.get("_opportunity_score", 0.5)

            # Look up strategy ID
            strat = db.get_strategy(strategy_name)
            strategy_id = strat["id"] if strat else 1

            # Save strategy run
            run_id = db.save_strategy_run(strategy_id, query_text, q.get("bilibili_check"))

            # Simulated YouTube search
            results = MOCK_YOUTUBE_RESULTS.get(query_text, [])

            print(f"\n  Query: \"{query_text}\"")
            print(f"  Strategy: {strategy_name} | Opportunity: {opp:.2f}")

            if results:
                best = max(results, key=lambda r: r["views"])
                avg_views = sum(r["views"] for r in results) // len(results)

                # Record yield
                tracker.record_query_yield(run_id, len(results), avg_views, best)

                print(f"  Found {len(results)} video(s):\n")
                for j, r in enumerate(results, 1):
                    like_ratio = r["likes"] / max(r["views"], 1)
                    cat_name = YT_CATEGORIES.get(r["category_id"], str(r["category_id"]))
                    print(f"    {j}. \"{r['title']}\"")
                    print(f"       Channel  : {r['channel']}")
                    print(f"       Views    : {r['views']:>12,}")
                    print(f"       Likes    : {r['likes']:>12,}  ({like_ratio:.1%} like ratio)")
                    print(f"       Duration : {fmt_duration(r['duration_seconds'])} ({r['duration_seconds']}s)")
                    print(f"       Category : {cat_name} (id={r['category_id']})")
                    print(f"       Uploaded : {r.get('upload_date', 'N/A')}")
                    print()

                # Add to aggregator
                for r in results:
                    aggregator.add(
                        video_id=r["id"],
                        title=r["title"],
                        channel=r["channel"],
                        views=r["views"],
                        likes=r["likes"],
                        duration_seconds=r["duration_seconds"],
                        category_id=r["category_id"],
                        opportunity_score=opp,
                        strategy=strategy_name,
                        query=query_text,
                    )
            else:
                tracker.record_query_yield(run_id, 0, 0, None)
                print(f"  Found 0 videos (query skipped — topic was saturated)\n")

            # Update strategy yield stats
            tracker.update_strategy_yield_stats(strategy_id)

        # ── Heuristic Scoring ──
        print_section("Heuristic Scoring (all candidates)")
        candidates = aggregator.get_candidates(min_views=params.youtube_min_views)

        print(f"\n  Total unique candidates : {aggregator.count()}")
        print(f"  Above {params.youtube_min_views:,} views    : {len(candidates)}")
        print(f"\n  Scoring formula:")
        print(f"    score = (engagement × {params.engagement_weight} + view_signal × {params.view_signal_weight}"
              f" + opportunity × {params.opportunity_weight} + duration × {params.duration_weight}) × category_bonus")
        print(f"    engagement = min(1.0, like_ratio / {params.engagement_good_threshold})")
        print(f"    view_signal = min(1.0, log1p(views) / log1p(1,000,000))")
        print(f"    duration = 1.0 if {fmt_duration(params.duration_sweet_spot[0])}–{fmt_duration(params.duration_sweet_spot[1])}, else 0.7")

        scored = []
        for c in candidates:
            score = heuristic_score(
                c.views, c.likes, c.duration_seconds, c.category_id,
                c.opportunity_score, params,
            )
            scored.append((c, score))

        scored.sort(key=lambda x: x[1], reverse=True)

        print(f"\n  Ranked Results:")
        print(f"  {'#':<3} {'Score':<8} {'Title':<50} {'Channel':<18} {'Views':>12} {'Like%':>6} {'Dur':>7} {'Opp':>5} {'Cat':>5}")
        print(f"  {'─'*3} {'─'*8} {'─'*50} {'─'*18} {'─'*12} {'─'*6} {'─'*7} {'─'*5} {'─'*5}")

        for rank, (c, score) in enumerate(scored, 1):
            like_ratio = c.likes / max(c.views, 1)
            cat_bonus = params.category_bonuses.get(c.category_id, 1.0)
            print(f"  {rank:<3} {score:<8.4f} {c.title[:50]:<50} {c.channel:<18} {c.views:>12,} {like_ratio:>5.1%} {fmt_duration(c.duration_seconds):>7} {c.opportunity_score:>5.2f} {cat_bonus:>5.2f}")

        # Show scoring breakdown for top 3
        print_section("Detailed Scoring Breakdown (top 3)")
        for rank, (c, score) in enumerate(scored[:3], 1):
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

        # ── Transportability Check ──
        print_section("Transportability Check (LLM, top 3)")
        for rank, (c, score) in enumerate(scored[:3], 1):
            result = check_transportability(
                backend, c.title, c.channel, c.duration_seconds, c.category_id,
            )
            status = "PASS" if result["transportable"] else "FAIL"
            print(f"\n  #{rank} \"{c.title[:60]}\"")
            print(f"    Verdict   : {status}")
            print(f"    Reasoning : {result['reasoning']}")

        # ================================================================
        # PHASE D: Yield Reflection (Loop 1 — fast feedback)
        # ================================================================
        print_header("PHASE D: Yield Reflection (Loop 1 — fast feedback)")

        yield_data = db.get_latest_run_yields(limit=10)
        strategy_stats = db.get_strategy_yield_stats()

        print(f"  Runs recorded this session: {len(yield_data)}")
        print(f"\n  Strategy yield rates:")
        for s in strategy_stats:
            if s["total_queries"] > 0:
                bar_len = int(s["yield_rate"] * 20)
                bar = "█" * bar_len + "░" * (20 - bar_len)
                print(f"    {s['name']:<35} {bar} {s['yield_rate']:>5.0%}  ({s['yielded_queries']}/{s['total_queries']})")

        # Run yield reflection
        print_section("LLM Yield Reflection")
        reflection = skill.reflect_on_yield(yield_data, strategy_stats)
        if reflection:
            print(f"\n  Analysis:")
            # Word-wrap the analysis
            analysis = reflection.get("analysis", "")
            words = analysis.split()
            line = "    "
            for w in words:
                if len(line) + len(w) + 1 > 80:
                    print(line)
                    line = "    " + w
                else:
                    line += " " + w if line.strip() else "    " + w
            if line.strip():
                print(line)

            yt_updated = reflection.get("updated_youtube_principles")
            print(f"\n  YouTube principles updated: {'YES — new version saved' if yt_updated else 'no change'}")
            if yt_updated:
                print(f"\n  New YouTube principles:")
                for line in yt_updated.strip().split("\n"):
                    print(f"    {line}")

            channels = reflection.get("channels_to_follow", [])
            if channels:
                print(f"\n  New channels to follow ({len(channels)}):")
                for ch in channels:
                    print(f"    {ch.get('channel_name', '?')}: {ch.get('reason', '')}")

        skill_row = db.get_skill("strategy_generation")
        print(f"\n  Skill version: v{skill.version} -> v{skill_row['version']}")

        # ================================================================
        # PHASE E: Transport + Outcome Tracking (Loop 2 — slow feedback)
        # ================================================================
        print_header("PHASE E: Transport Simulation + Outcome Tracking (Loop 2)")

        print(f"  Simulating: we transported the top 2 scored videos to Bilibili\n")

        transported = [
            ("yt_byd_01", "BV1byd1234", 220_000,
             "I've Driven a BYD Seal for 6 Months - Here's the TRUTH"),
            ("yt_mapo_01", "BV1mapo567", 185_000,
             "American Chef Tries REAL Sichuan Mapo Tofu (Mind Blown)"),
        ]

        for yt_id, bvid, bili_views, title in transported:
            tracker.mark_transported(yt_id, bvid)
            tracker.update_bilibili_views(bvid, bili_views)
            outcome = "SUCCESS" if bili_views >= params.bilibili_success_threshold else "FAILURE"
            icon = "✓" if outcome == "SUCCESS" else "✗"
            print(f"  {icon} {title[:55]}")
            print(f"    YouTube ID  : {yt_id}")
            print(f"    Bilibili BV : {bvid}")
            print(f"    Bili views  : {bili_views:,}")
            print(f"    Threshold   : {params.bilibili_success_threshold:,}")
            print(f"    Outcome     : {outcome}")
            print()

        # Run outcome reflection (Loop 2)
        print_section("LLM Outcome Reflection (Strategy Skill)")
        outcomes = db.get_latest_run_yields(limit=10)
        transported_outcomes = [o for o in outcomes if o.get("was_transported")]

        if transported_outcomes:
            outcome_reflection = skill.reflect_on_outcomes(transported_outcomes)
            if outcome_reflection:
                print(f"\n  Analysis:")
                analysis = outcome_reflection.get("analysis", "")
                words = analysis.split()
                line = "    "
                for w in words:
                    if len(line) + len(w) + 1 > 80:
                        print(line)
                        line = "    " + w
                    else:
                        line += " " + w if line.strip() else "    " + w
                if line.strip():
                    print(line)

                bili_updated = outcome_reflection.get("updated_bilibili_principles")
                print(f"\n  Bilibili principles updated: {'YES — new version saved' if bili_updated else 'no change'}")
                if bili_updated:
                    print(f"\n  New Bilibili principles:")
                    for line in bili_updated.strip().split("\n"):
                        print(f"    {line}")

                scoring = outcome_reflection.get("scoring_insights", "")
                if scoring:
                    print(f"\n  Scoring insights:")
                    words = scoring.split()
                    line = "    "
                    for w in words:
                        if len(line) + len(w) + 1 > 80:
                            print(line)
                            line = "    " + w
                        else:
                            line += " " + w if line.strip() else "    " + w
                    if line.strip():
                        print(line)

        # Market skill reflection
        print_section("LLM Market Reflection (Market Analysis Skill)")
        market_reflection = market_skill.reflect_on_outcomes(transported_outcomes)
        if market_reflection:
            print(f"\n  Analysis:")
            analysis = market_reflection.get("analysis", "")
            words = analysis.split()
            line = "    "
            for w in words:
                if len(line) + len(w) + 1 > 80:
                    print(line)
                    line = "    " + w
                else:
                    line += " " + w if line.strip() else "    " + w
            if line.strip():
                print(line)

            criteria = market_reflection.get("updated_criteria")
            if criteria:
                print(f"\n  Updated saturation criteria:")
                for line in criteria.strip().split("\n"):
                    print(f"    {line}")

        # ================================================================
        # SUMMARY
        # ================================================================
        print_header("FINAL SUMMARY")

        final_strategies = db.list_strategies(active_only=True)
        final_channels = db.list_followed_channels()
        strategy_skill = db.get_skill("strategy_generation")
        market_skill_row = db.get_skill("market_analysis")

        print(f"""
  Pipeline Statistics
  ───────────────────
  LLM calls made         : {backend._call_count}
  Queries generated      : {len(queries)}
  Passed saturation      : {len(validated_queries)}/{len(queries)}
  YouTube videos found   : {aggregator.count()}
  Above min-view filter  : {len(candidates)}/{aggregator.count()}
  Videos transported     : {len(transported)}
  Transport success rate : {sum(1 for _, _, v, _ in transported if v >= params.bilibili_success_threshold)}/{len(transported)}

  System State
  ───────────────────
  Active strategies      : {len(final_strategies)}
  Followed channels      : {len(final_channels)}
  Strategy skill version : v{strategy_skill['version']} (started at v1)
  Market skill version   : v{market_skill_row['version']} (started at v1)
""")

        print(f"  LLM Call Log:")
        for i, label in enumerate(backend._call_log, 1):
            print(f"    {i:>2}. {label}")

        print(f"\n  Strategy Yield Summary:")
        for s in db.get_strategy_yield_stats():
            if s["total_queries"] > 0:
                bar_len = int(s["yield_rate"] * 20)
                bar = "█" * bar_len + "░" * (20 - bar_len)
                print(f"    {s['name']:<35} {bar} {s['yield_rate']:>5.0%}")

        print_section("Final YouTube Principles (evolved)")
        for line in skill.youtube_principles.strip().split("\n"):
            print(f"  {line}")

        print_section("Final Bilibili Principles (evolved)")
        for line in skill.bilibili_principles.strip().split("\n"):
            print(f"  {line}")

    finally:
        db.close()

    print(f"\n{'=' * 76}")
    print(f"  Mock run complete!")
    print(f"{'=' * 76}\n")


if __name__ == "__main__":
    main()
