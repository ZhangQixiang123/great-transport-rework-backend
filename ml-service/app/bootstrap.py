"""Bootstrap — one-time setup for the skill-based discovery framework.

Analyzes historical competitor data, seeds strategies and channels,
derives scoring parameters, and initializes skill prompts.
"""
import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# 8 initial strategies from discovery/strategies.py definitions
INITIAL_STRATEGIES = [
    {
        "name": "foreign_appreciation",
        "description": "Western creators showing genuine appreciation/analysis of Chinese culture, products, food, technology.",
        "example_queries": json.dumps(["foreigner tries Chinese street food", "western review Chinese EV"]),
        "youtube_channels": json.dumps(["@thefoodranger", "@xiaomanyc", "@serpentza"]),
        "youtube_categories": json.dumps([19, 22]),
        "search_tips": "Add reaction words: 'honest', 'first time', 'tries'.",
        "bilibili_check": "外国人 中国",
        "audience_notes": "Chinese audiences love genuine positive reactions.",
    },
    {
        "name": "educational_explainer",
        "description": "High-quality educational content on universal topics (science, space, history, engineering).",
        "example_queries": json.dumps(["how black holes work explained", "engineering mega projects"]),
        "youtube_channels": json.dumps(["@kurzgesagt", "@veritasium", "@3blue1brown"]),
        "youtube_categories": json.dumps([27, 28]),
        "search_tips": "Visual-heavy, language-independent content works best.",
        "bilibili_check": "科普 英文",
        "audience_notes": "Bilibili has a strong science/knowledge community.",
    },
    {
        "name": "cultural_comparison",
        "description": "Respectful comparison of how things differ between cultures.",
        "example_queries": json.dumps(["daily life difference America China", "school system comparison"]),
        "youtube_channels": json.dumps([]),
        "youtube_categories": json.dumps([22, 24]),
        "search_tips": "Focus on respectful, balanced comparisons.",
        "bilibili_check": "中外对比",
        "audience_notes": "Audiences love seeing daily life differences.",
    },
    {
        "name": "chinese_brand_foreign_review",
        "description": "Foreign creators reviewing Chinese brands/products (Huawei, BYD, Xiaomi, DJI).",
        "example_queries": json.dumps(["BYD review honest", "Huawei phone review 2026"]),
        "youtube_channels": json.dumps(["@mkbhd", "@mrwhosetheboss"]),
        "youtube_categories": json.dumps([28]),
        "search_tips": "Chinese audiences love seeing international recognition of Chinese brands.",
        "bilibili_check": "外国人 评测 中国品牌",
        "audience_notes": "National pride content performs well.",
    },
    {
        "name": "skill_talent_showcase",
        "description": "Pure skill/talent videos — music, art, cooking, engineering, sports.",
        "example_queries": json.dumps(["incredible woodworking project", "street musician amazing performance"]),
        "youtube_channels": json.dumps([]),
        "youtube_categories": json.dumps([10, 26]),
        "search_tips": "Visual content that transcends language barriers.",
        "bilibili_check": "技术流 外国",
        "audience_notes": "Skill-based content has universal appeal.",
    },
    {
        "name": "behind_the_scenes",
        "description": "Factory tours, movie production, game development BTS.",
        "example_queries": json.dumps(["how factory makes product", "game development behind scenes"]),
        "youtube_channels": json.dumps([]),
        "youtube_categories": json.dumps([28, 22]),
        "search_tips": "Satisfying, visual, universally appealing process content.",
        "bilibili_check": "幕后 制作过程",
        "audience_notes": "BTS content satisfies curiosity-driven audiences.",
    },
    {
        "name": "challenge_experiment",
        "description": "Scientific experiments, building challenges, survival projects.",
        "example_queries": json.dumps(["building challenge extreme", "science experiment unexpected result"]),
        "youtube_channels": json.dumps(["@markrober", "@mrBeast"]),
        "youtube_categories": json.dumps([24, 28]),
        "search_tips": "High entertainment value, visual, universal appeal.",
        "bilibili_check": "挑战 实验",
        "audience_notes": "Challenge content is popular across cultures.",
    },
    {
        "name": "global_trending_chinese_angle",
        "description": "Global trending events/topics analyzed from a perspective that resonates with Chinese audiences.",
        "example_queries": json.dumps(["tech industry analysis 2026", "AI impact society"]),
        "youtube_channels": json.dumps([]),
        "youtube_categories": json.dumps([25, 28]),
        "search_tips": "Tech drama and industry analysis get high engagement.",
        "bilibili_check": "外网热议",
        "audience_notes": "Chinese audiences want to see global perspectives.",
    },
]


def run_bootstrap(db, backend=None, skip_llm: bool = False) -> dict:
    """Run the full bootstrap sequence.

    Args:
        db: Connected Database instance.
        backend: Optional LLMBackend for principle generation.
        skip_llm: If True, skip LLM calls (use defaults).

    Returns:
        Summary dict of what was bootstrapped.
    """
    result = {"strategies_seeded": 0, "channels_seeded": 0, "scoring_bootstrapped": False}

    # Step 1: Ensure tables exist
    db.ensure_skill_tables()

    # Step 2: Seed strategies
    result["strategies_seeded"] = _seed_strategies(db)

    # Step 3: Bootstrap scoring params
    result["scoring_bootstrapped"] = _bootstrap_scoring(db)

    # Step 4: Seed followed channels
    result["channels_seeded"] = _seed_followed_channels(db)

    # Step 5: Seed default skill entries
    _seed_skills(db)

    # Step 6: Optionally use LLM to analyze data and write initial principles
    if backend and not skip_llm:
        result["llm_principles"] = _bootstrap_principles(db, backend)
    else:
        result["llm_principles"] = False

    return result


def _seed_skills(db) -> None:
    """Seed default skill entries so skill-show works immediately."""
    from .skills.strategy_generation import StrategyGenerationSkill
    from .skills.market_analysis import MarketAnalysisSkill

    for SkillClass in [StrategyGenerationSkill, MarketAnalysisSkill]:
        # Instantiate with a dummy backend — this triggers _load_from_db
        # which seeds defaults if not found
        class _DummyBackend:
            def chat(self, messages, json_schema=None):
                return '{}'
        try:
            SkillClass(db=db, backend=_DummyBackend())
        except Exception:
            pass  # Skill may already exist


def _seed_strategies(db) -> int:
    """Seed the 8 initial strategies from hardcoded definitions."""
    count = 0
    for s in INITIAL_STRATEGIES:
        existing = db.get_strategy(s["name"])
        if existing:
            continue
        db.add_strategy(
            name=s["name"],
            description=s["description"],
            example_queries=s.get("example_queries"),
            youtube_channels=s.get("youtube_channels"),
            youtube_categories=s.get("youtube_categories"),
            search_tips=s.get("search_tips"),
            bilibili_check=s.get("bilibili_check"),
            audience_notes=s.get("audience_notes"),
            source="bootstrap",
        )
        count += 1
    logger.info("Seeded %d strategies", count)
    return count


def _bootstrap_scoring(db) -> bool:
    """Bootstrap scoring parameters from competitor data."""
    try:
        from .scoring.heuristic import bootstrap_scoring_params
        params = bootstrap_scoring_params(db, source="competitor")
        db.save_scoring_params(params.to_json(), source="competitor")
        logger.info("Bootstrapped scoring params: threshold=%d", params.bilibili_success_threshold)
        return True
    except Exception as e:
        logger.warning("Scoring bootstrap failed (no data?): %s", e)
        # Save defaults
        from .scoring.heuristic import ScoringParams
        db.save_scoring_params(ScoringParams().to_json(), source="default")
        return False


def _seed_followed_channels(db) -> int:
    """Seed followed channels from competitor transport data."""
    if not db._conn:
        return 0

    try:
        rows = db._conn.execute("""
            SELECT ys.yt_channel_title, COUNT(*) as transport_count
            FROM competitor_videos cv
            JOIN youtube_stats ys ON cv.youtube_source_id = ys.youtube_id
            WHERE cv.views > 0 AND ys.yt_channel_title IS NOT NULL
                  AND ys.yt_channel_title != ''
            GROUP BY ys.yt_channel_title
            HAVING transport_count >= 3
            ORDER BY transport_count DESC
        """).fetchall()
    except Exception:
        # youtube_stats table may not exist
        logger.info("No youtube_stats data — skipping channel seeding.")
        return 0

    count = 0
    for r in rows:
        channel_name = r["yt_channel_title"]
        transport_count = r["transport_count"]
        db.add_followed_channel(
            channel_name=channel_name,
            reason=f"Competitor data: {transport_count} transports",
            source="bootstrap",
        )
        count += 1

    logger.info("Seeded %d followed channels", count)
    return count


def _bootstrap_principles(db, backend) -> bool:
    """Use LLM to analyze historical data and write initial skill principles."""
    if not db._conn:
        return False

    # Aggregate competitor data for LLM analysis
    try:
        rows = db._conn.execute("""
            SELECT ys.yt_category_id, ys.yt_channel_title,
                   ys.yt_views, ys.yt_likes, ys.yt_duration_seconds,
                   cv.views as bili_views
            FROM competitor_videos cv
            JOIN youtube_stats ys ON cv.youtube_source_id = ys.youtube_id
            WHERE cv.views > 0 AND ys.yt_views > 0
        """).fetchall()
    except Exception:
        logger.info("No youtube_stats data — skipping LLM principles bootstrap.")
        return False

    if not rows:
        return False

    rows = [dict(r) for r in rows]

    # Compute simple aggregates
    total = len(rows)
    categories = {}
    for r in rows:
        cat = r.get("yt_category_id")
        if cat:
            categories.setdefault(cat, []).append(r["bili_views"])

    category_summary = []
    for cat, views in sorted(categories.items(), key=lambda x: len(x[1]), reverse=True)[:10]:
        avg_views = sum(views) / len(views)
        category_summary.append(f"  Category {cat}: {len(views)} transports, avg {avg_views:,.0f} Bilibili views")

    prompt = (
        f"Analyze this historical YouTube-to-Bilibili transport data ({total} transports) "
        "and write discovery principles.\n\n"
        "## Performance by YouTube Category (top 10)\n"
        + "\n".join(category_summary) + "\n\n"
        "Write TWO sections of principles:\n"
        "1. YouTube search principles — what characteristics predict transport success\n"
        "2. Bilibili audience principles — what content types get the most views\n\n"
        "Respond with JSON:\n"
        '{\n'
        '  "youtube_principles": "...",\n'
        '  "bilibili_principles": "..."\n'
        '}'
    )

    try:
        response = backend.chat(
            messages=[
                {"role": "system", "content": "You analyze transport data. Respond in JSON."},
                {"role": "user", "content": prompt},
            ],
        )
        result = json.loads(response)

        # Store as the initial skill prompts
        from .skills.strategy_generation import StrategyGenerationSkill, DEFAULT_YOUTUBE_PRINCIPLES, DEFAULT_BILIBILI_PRINCIPLES
        yt_principles = result.get("youtube_principles", DEFAULT_YOUTUBE_PRINCIPLES)
        bili_principles = result.get("bilibili_principles", DEFAULT_BILIBILI_PRINCIPLES)

        system_prompt = (
            "You are an expert at finding specific YouTube videos that will succeed when "
            "transported to Bilibili. Your primary job is crafting effective YouTube search "
            "queries -- the right query finds the right video.\n\n"
            "YouTube search principles you've learned:\n"
            f"{yt_principles}\n\n"
            "Bilibili audience principles you've learned:\n"
            f"{bili_principles}\n\n"
            "Respond in the exact JSON format requested."
        )

        db.upsert_skill(
            "strategy_generation",
            system_prompt,
            StrategyGenerationSkill(
                db=db, backend=backend
            ).prompt_template if False else "",  # Don't recurse
            json.dumps({"type": "object"}),
        )
        # Actually let's just update the system prompt directly
        skill_row = db.get_skill("strategy_generation")
        if skill_row:
            db.update_skill_prompt(
                "strategy_generation", system_prompt, skill_row["prompt_template"],
            )

        logger.info("Bootstrapped LLM principles from competitor data")
        return True

    except Exception as e:
        logger.warning("LLM principles bootstrap failed: %s", e)
        return False
