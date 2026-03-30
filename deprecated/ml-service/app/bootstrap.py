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
        "name": "gaming_deep_dive",
        "description": "In-depth game reviews, industry controversies, broken launches, esports drama, competitive gaming analysis. Opinionated content with strong takes.",
        "example_queries": json.dumps(["Starfield was a disaster honest review", "worst game launches that killed studios", "esports match fixing scandal exposed"]),
        "youtube_channels": json.dumps(["@AngryJoeShow", "@videogamedunkey", "@SkillUp"]),
        "youtube_categories": json.dumps([20]),
        "search_tips": "Focus on controversy, drama, and strong opinions over gameplay footage.",
        "bilibili_check": "游戏评测 搬运",
        "audience_notes": "Gaming is huge on Bilibili. Controversy and hot takes drive engagement.",
    },
    {
        "name": "educational_explainer",
        "description": "High-quality educational content on universal topics (science, space, history, engineering).",
        "example_queries": json.dumps(["why the ocean is still unexplored", "how bridges are built over deep water", "the engineering behind the tallest building"]),
        "youtube_channels": json.dumps(["@kurzgesagt", "@veritasium", "@3blue1brown"]),
        "youtube_categories": json.dumps([27, 28]),
        "search_tips": "Visual-heavy, language-independent content works best.",
        "bilibili_check": "科普 英文",
        "audience_notes": "Bilibili has a strong science/knowledge community.",
    },
    {
        "name": "tech_teardown",
        "description": "In-depth electronics reviews, product teardowns, tech comparisons, planned obsolescence exposés, right-to-repair advocacy. Strong opinions, not unboxing fluff.",
        "example_queries": json.dumps(["iPhone teardown actual cost to make", "why your phone slows down on purpose", "right to repair Apple lost"]),
        "youtube_channels": json.dumps(["@JerryRigEverything", "@LouisRossmann", "@MKBHD"]),
        "youtube_categories": json.dumps([28]),
        "search_tips": "Look for teardowns, exposés, and strong consumer advocacy angles.",
        "bilibili_check": "数码评测 搬运",
        "audience_notes": "Tech criticism and consumerism critique resonate strongly.",
    },
    {
        "name": "chinese_brand_foreign_review",
        "description": "Foreign creators reviewing Chinese brands/products (Huawei, BYD, Xiaomi, DJI).",
        "example_queries": json.dumps(["BYD seal test drive honest opinion", "foreigner tries Xiaomi for the first time", "DJI drone vs American competitor"]),
        "youtube_channels": json.dumps(["@mkbhd", "@mrwhosetheboss"]),
        "youtube_categories": json.dumps([28]),
        "search_tips": "Chinese audiences love seeing international recognition of Chinese brands.",
        "bilibili_check": "外国人 评测 中国品牌",
        "audience_notes": "National pride content performs well.",
    },
    {
        "name": "social_commentary",
        "description": "Societal debates, internet culture analysis, social experiments, workplace/generational issues. Content where creator takes a strong stance worth amplifying or countering.",
        "example_queries": json.dumps(["giving strangers $1000 to see what happens", "why nobody talks to each other anymore", "the loneliness epidemic is getting worse"]),
        "youtube_channels": json.dumps([]),
        "youtube_categories": json.dumps([22, 24, 25]),
        "search_tips": "Look for creators who take strong stances on divisive topics.",
        "bilibili_check": "社会现象 外网",
        "audience_notes": "Controversy drives bullet comments. The more debatable the better.",
    },
    {
        "name": "geopolitics_hot_take",
        "description": "International relations analysis, trade war breakdowns, sanctions impact, military/defense commentary. Political hot takes Chinese audiences care about.",
        "example_queries": json.dumps(["why US sanctions are backfiring explained", "the real reason China is winning in Africa", "NATO expansion consequences nobody talks about"]),
        "youtube_channels": json.dumps(["@CaspianReport", "@PolyMatter", "@RealLifeLore"]),
        "youtube_categories": json.dumps([25, 27]),
        "search_tips": "Geopolitical analysis with clear takes, not dry news recaps.",
        "bilibili_check": "国际政治 分析",
        "audience_notes": "Chinese audiences deeply engaged with geopolitics, especially US-China dynamics.",
    },
    {
        "name": "challenge_experiment",
        "description": "Scientific experiments, building challenges, survival projects.",
        "example_queries": json.dumps(["I survived 24 hours in the wilderness with nothing", "building the strongest bridge out of popsicle sticks", "what happens if you microwave everything"]),
        "youtube_channels": json.dumps(["@markrober", "@mrBeast"]),
        "youtube_categories": json.dumps([24, 28]),
        "search_tips": "High entertainment value, visual, universal appeal.",
        "bilibili_check": "挑战 实验",
        "audience_notes": "Challenge content is popular across cultures.",
    },
    {
        "name": "global_trending_chinese_angle",
        "description": "Global trending events/topics analyzed from a perspective that resonates with Chinese audiences.",
        "example_queries": json.dumps(["why everyone is mass quitting their jobs", "the AI tool that replaced 300 workers", "Boeing keeps getting worse and nobody cares"]),
        "youtube_channels": json.dumps([]),
        "youtube_categories": json.dumps([25, 28]),
        "search_tips": "Tech drama and industry analysis get high engagement.",
        "bilibili_check": "外网热议",
        "audience_notes": "Chinese audiences want to see global perspectives.",
    },
    {
        "name": "surveillance_dashcam",
        "description": "Dashcam footage, surveillance clips, livestream fails/wins. The 神人TV genre — incredible or absurd real-life moments caught on camera.",
        "example_queries": json.dumps(["dashcam footage you won't believe is real", "security camera caught the most insane moment", "forklift operator destroys entire warehouse"]),
        "youtube_channels": json.dumps(["@FailArmy", "@DashCamNation"]),
        "youtube_categories": json.dumps([24, 22]),
        "search_tips": "Compilations and single-clip viral moments. Language-free, pure visual entertainment.",
        "bilibili_check": "监控 实况 神操作",
        "audience_notes": "Perfect for bullet-screen commentary. No language barrier, high shareability.",
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


def refresh_strategies(db) -> int:
    """Update existing strategies' example_queries from code constants.

    Only updates strategies that exist in both INITIAL_STRATEGIES and the DB.
    Does not create new strategies or touch LLM-proposed ones.

    Returns:
        Number of strategies updated.
    """
    updated = 0
    for s in INITIAL_STRATEGIES:
        existing = db.get_strategy(s["name"])
        if not existing:
            continue
        # Only update if example_queries differ
        new_queries = s.get("example_queries")
        if new_queries and existing.get("example_queries") != new_queries:
            db.update_strategy_metadata(s["name"], example_queries=new_queries)
            logger.info("Refreshed example_queries for '%s'", s["name"])
            updated += 1
    return updated


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
