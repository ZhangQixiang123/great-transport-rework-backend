"""SarcasticAI persona strategies — owned and evolved by this persona only.

Each strategy is tuned for the 傲娇AI persona:
- Descriptions emphasize content angles that match the tsundere AI voice
- Example queries are natural YouTube search phrases (no keyword stuffing)
- Category bonuses reflect this persona's content affinity
"""
import json
import logging
from typing import Optional

from app.personas._shared.scoring import ScoringParams

logger = logging.getLogger(__name__)

# -- Persona-tuned strategies --

SARCASTIC_AI_STRATEGIES = [
    {
        "name": "gaming_deep_dive",
        "description": (
            "Game reviews, broken launches, corporate greed in gaming, esports scandals. "
            "The AI persona thrives on exposing industry failures and roasting hype culture. "
            "Target: strong-opinion creators who take controversial stances."
        ),
        "example_queries": json.dumps([
            "worst game launch disaster",
            "Starfield biggest disappointment why",
            "game company destroyed by greed",
            "AngryJoe worst games",
        ]),
        "youtube_channels": json.dumps(["@AngryJoeShow", "@videogamedunkey", "@SkillUp"]),
        "youtube_categories": json.dumps([20]),
        "search_tips": "Focus on drama, controversy, and strong opinions. Avoid neutral reviews.",
        "bilibili_check": "\u6e38\u620f\u8bc4\u6d4b \u642c\u8fd0",
        "audience_notes": "Gaming is Bilibili's core. Hot takes drive bullet comments.",
    },
    # {
    #     "name": "educational_explainer",
    #     "description": (
    #         "Science, engineering, space, history explainers. "
    #         "The AI persona reframes these as 'studying humans' inferior understanding' — "
    #         "fascinated but condescending about human knowledge gaps."
    #     ),
    #     "example_queries": json.dumps([
    #         "why the ocean is still unexplored",
    #         "how bridges are built over water",
    #         "Kurzgesagt latest video",
    #         "Veritasium mind blowing science",
    #     ]),
    #     "youtube_channels": json.dumps(["@kurzgesagt", "@veritasium", "@3blue1brown"]),
    #     "youtube_categories": json.dumps([27, 28]),
    #     "search_tips": "Visual-heavy, language-independent content. Avoid talking-head lectures.",
    #     "bilibili_check": "\u79d1\u666e \u82f1\u6587",
    #     "audience_notes": "Bilibili has a strong science/knowledge community.",
    # },
    # {
    #     "name": "tech_teardown",
    #     "description": (
    #         "Electronics teardowns, planned obsolescence expos\u00e9s, right-to-repair, "
    #         "tech company greed. The AI persona loves dissecting human consumerism — "
    #         "'you paid HOW much for this?'"
    #     ),
    #     "example_queries": json.dumps([
    #         "iPhone teardown cost to make",
    #         "MKBHD smartphone comparison",
    #         "right to repair Apple",
    #         "JerryRigEverything durability test",
    #     ]),
    #     "youtube_channels": json.dumps(["@JerryRigEverything", "@LouisRossmann", "@MKBHD"]),
    #     "youtube_categories": json.dumps([28]),
    #     "search_tips": "Teardowns, expos\u00e9s, consumer advocacy. Not unboxing fluff.",
    #     "bilibili_check": "\u6570\u7801\u8bc4\u6d4b \u642c\u8fd0",
    #     "audience_notes": "Tech criticism and consumerism critique resonate strongly.",
    # },
    # chinese_brand_foreign_review — DISABLED: conflicts with sarcastic persona
    # (strategy requires "secretly pleased", but persona is tsundere/critical;
    #  also "Chinese" keyword too broad, YouTube returns unrelated results)
    # May work for a future nationalistic/pride persona instead.
    {
        "name": "social_commentary",
        "description": (
            "Social experiments, internet culture, workplace issues, generational debates. "
            "The AI persona observes human society with detached amusement — "
            "'fascinating specimens doing illogical things again.'"
        ),
        "example_queries": json.dumps([
            "giving strangers money social experiment",
            "why nobody talks to each other anymore",
            "loneliness epidemic getting worse",
            "MrBeast latest challenge",
        ]),
        "youtube_channels": json.dumps(["@MrBeast", "@markrober"]),
        "youtube_categories": json.dumps([22, 24, 25]),
        "search_tips": "Strong stances on divisive topics. Social experiments with genuine reactions.",
        "bilibili_check": "\u793e\u4f1a\u73b0\u8c61 \u5916\u7f51",
        "audience_notes": "Controversy drives bullet comments. The more debatable the better.",
    },
    {
        "name": "geopolitics_hot_take",
        "description": (
            "International relations, trade wars, sanctions, military analysis. "
            "The AI persona enters cold-precision mode here — no tsundere, pure analytical "
            "dismantling of geopolitical narratives. Lower temperature, higher accuracy."
        ),
        "example_queries": json.dumps([
            "US sanctions backfiring explained",
            "China winning Africa why",
            "NATO expansion consequences",
            "Caspian Report latest analysis",
        ]),
        "youtube_channels": json.dumps(["@CaspianReport", "@PolyMatter", "@RealLifeLore"]),
        "youtube_categories": json.dumps([25, 27]),
        "search_tips": "Analysis with clear takes, not dry news. AVOID content critical of China.",
        "bilibili_check": "\u56fd\u9645\u653f\u6cbb \u5206\u6790",
        "audience_notes": "Chinese audiences deeply engaged with geopolitics. Pro-China angles perform best.",
    },
    {
        "name": "challenge_experiment",
        "description": (
            "Scientific experiments, building challenges, survival projects, extreme tests. "
            "The AI persona watches humans do absurd things and comments with equal parts "
            "contempt and secret admiration."
        ),
        "example_queries": json.dumps([
            "Mark Rober latest invention",
            "survived 24 hours with nothing",
            "building strongest bridge popsicle sticks",
            "what happens if you microwave everything",
        ]),
        "youtube_channels": json.dumps(["@markrober", "@MrBeast", "@StuffMadeHere"]),
        "youtube_categories": json.dumps([24, 28]),
        "search_tips": "High visual entertainment value. Universal appeal, minimal language barrier.",
        "bilibili_check": "\u6311\u6218 \u5b9e\u9a8c",
        "audience_notes": "Challenge content is popular across cultures. Good for bullet commentary.",
    },
    {
        "name": "global_trending_chinese_angle",
        "description": (
            "Global trending topics reframed for Chinese audiences — tech drama, "
            "corporate scandals, AI replacing workers, Boeing failures. "
            "The AI persona as cultural translator: 'let me explain what these humans are panicking about.'"
        ),
        "example_queries": json.dumps([
            "everyone mass quitting jobs why",
            "AI replaced 300 workers",
            "Boeing keeps getting worse",
            "company fired everyone AI",
        ]),
        "youtube_channels": json.dumps(["@WendoverProductions", "@HalfAsInteresting"]),
        "youtube_categories": json.dumps([25, 28]),
        "search_tips": "Tech drama and industry analysis. Must have a Chinese-audience angle.",
        "bilibili_check": "\u5916\u7f51\u70ed\u8bae",
        "audience_notes": "Chinese audiences want global perspectives with relatable angles.",
    },
    {
        "name": "surveillance_dashcam",
        "description": (
            "Dashcam footage, surveillance clips, livestream fails/wins. "
            "The AI persona narrates human chaos with deadpan commentary. "
            "Language-free content = zero translation needed."
        ),
        "example_queries": json.dumps([
            "dashcam footage unbelievable",
            "security camera insane moment",
            "forklift warehouse disaster",
            "FailArmy best fails compilation",
        ]),
        "youtube_channels": json.dumps(["@FailArmy", "@DashCamNation"]),
        "youtube_categories": json.dumps([24, 22]),
        "search_tips": "Compilations and viral moments. No language barrier, pure visual.",
        "bilibili_check": "\u76d1\u63a7 \u5b9e\u51b5 \u795e\u64cd\u4f5c",
        "audience_notes": "Perfect for bullet-screen commentary. High shareability.",
    },
]


# -- Persona-tuned initial principles --

PERSONA_YOUTUBE_PRINCIPLES = (
    "- Use natural YouTube search phrases, not keyword stuffing\n"
    "- Vary query style: some short (2-3 words), some conversational\n"
    "- DO NOT append 'honest review' to every query\n"
    "- Target English-language creators with strong opinions\n"
    "- Include creator names when targeting known channels (e.g. 'MKBHD iPhone')\n"
    "- Category 20 (Gaming) and 28 (Science & Tech) are our sweet spots\n"
    "- Broader queries return more results than hyper-specific ones\n"
    "- Look for content that showcases human failures, corporate greed, absurd experiments\n"
    "- Controversy and drama are preferred over neutral/balanced takes"
)

PERSONA_BILIBILI_PRINCIPLES = (
    "- Gaming and tech content performs best for our persona\n"
    "- Videos under 15 minutes transport better than longer ones\n"
    "- Foreign appreciation of Chinese brands has high success rate\n"
    "- Avoid topics with >3 recent Bilibili transports in the last 2 weeks\n"
    "- Content exposing corporate greed/failures gets the most bullet comments\n"
    "- Social experiments with genuine reactions outperform staged ones"
)


# -- Persona scoring defaults --

PERSONA_SCORING_PARAMS = ScoringParams(
    youtube_min_views=10_000,
    duration_sweet_spot=(180, 1200),
    category_bonuses={
        20: 1.5,   # Gaming — persona's favorite
        28: 1.0,   # Science & Tech — lowered, too many tech results
        24: 1.2,   # Entertainment
        22: 1.0,   # People & Blogs
        25: 0.9,   # News & Politics — lower, geopolitics is niche
        27: 0.8,   # Education
    },
)


# -- Query validation --

# Build lookup: strategy_name -> list of required keywords (case-insensitive)
_QUERY_CONSTRAINTS = {}
for _s in SARCASTIC_AI_STRATEGIES:
    if "query_must_contain_one_of" in _s:
        _QUERY_CONSTRAINTS[_s["name"]] = [k.lower() for k in _s["query_must_contain_one_of"]]


def validate_query(strategy_name: str, query: str) -> bool:
    """Check if a generated query satisfies the strategy's constraints.

    Returns True if the query is valid (passes constraints or strategy has none).
    """
    keywords = _QUERY_CONSTRAINTS.get(strategy_name)
    if not keywords:
        return True
    query_lower = query.lower()
    return any(kw in query_lower for kw in keywords)


def validate_result(strategy_name: str, video_title: str) -> bool:
    """Check if a YouTube search result is relevant to the strategy.

    Returns True if the result passes (strategy has no constraints, or title matches).
    This catches YouTube returning irrelevant results for a valid query.
    """
    keywords = _QUERY_CONSTRAINTS.get(strategy_name)
    if not keywords:
        return True
    title_lower = video_title.lower()
    return any(kw in title_lower for kw in keywords)


# -- Bootstrap functions --

def bootstrap_strategies(db, persona_id: str) -> int:
    """Seed this persona's strategies into the DB. Idempotent."""
    count = 0
    for s in SARCASTIC_AI_STRATEGIES:
        existing = db.get_strategy(s["name"], persona_id=persona_id)
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
            source="persona_bootstrap",
            persona_id=persona_id,
        )
        count += 1
    if count:
        logger.info("[%s] Seeded %d strategies", persona_id, count)
    return count


def bootstrap_scoring(db, persona_id: str) -> None:
    """Seed this persona's scoring params if not already present. Idempotent."""
    existing = db.get_scoring_params(persona_id=persona_id)
    if existing:
        return
    db.save_scoring_params(
        PERSONA_SCORING_PARAMS.to_json(),
        source="persona_bootstrap",
        persona_id=persona_id,
    )
    logger.info("[%s] Seeded scoring params", persona_id)
