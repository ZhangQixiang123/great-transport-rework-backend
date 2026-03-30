"""LLM transportability check — fixed prompt, not a skill (yet)."""

import json
import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── Hard content safety filter (pre-LLM) ────────────────────────────
# These patterns catch content that must NEVER be uploaded to Bilibili,
# regardless of what the LLM thinks.  Case-insensitive title matching.

_BLOCK_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Chinese leadership / government criticism → instant Bilibili ban
    (re.compile(r"xi\s*jinping|习近平|시진핑|jinping", re.IGNORECASE),
     "Content about Chinese leadership — Bilibili ban risk"),
    (re.compile(r"\bCCP\b|chinese\s+communist\s+party|中共|共产党", re.IGNORECASE),
     "Content about CCP — Bilibili ban risk"),
    (re.compile(r"tiananmen|天安门事件|六四", re.IGNORECASE),
     "Politically sensitive historical event — Bilibili ban risk"),
    (re.compile(r"taiwan\s+independen|台独|藏独|疆独|uyghur\s+genoc", re.IGNORECASE),
     "Separatism/sovereignty content — Bilibili ban risk"),

    # Anti-China military framing
    (re.compile(r"china.{0,60}sitting\s+duck|china.{0,60}can.?t\s+win|destroy\s+china|china.{0,30}weak|china.{0,30}inferior", re.IGNORECASE),
     "Anti-China military framing — audience hostile"),

    # Antisemitism / religious hatred
    (re.compile(r"(why|world).{0,30}hate.{0,10}jews|antisemit", re.IGNORECASE),
     "Antisemitic content — hate speech"),
    (re.compile(r"jewish\s+(conspiracy|control|world\s+order)", re.IGNORECASE),
     "Antisemitic conspiracy content"),
    (re.compile(r"holocaust\s+(hoax|fake|didn.t\s+happen)", re.IGNORECASE),
     "Holocaust denial content"),

    # General hate speech patterns
    (re.compile(r"(why|should)\s+we\s+hate\s+\w+|race\s+war|white\s+(supremac|power|genocide)", re.IGNORECASE),
     "Hate speech content"),
]


def _check_content_safety(title: str) -> Optional[str]:
    """Pre-LLM hard filter.  Returns rejection reason or None if safe."""
    for pattern, reason in _BLOCK_PATTERNS:
        if pattern.search(title):
            return reason
    return None


TRANSPORTABILITY_PROMPT = """Is this YouTube video suitable for transport to Bilibili?

Title: {title}
Channel: {channel}
Duration: {duration_minutes:.1f} minutes
Category ID: {category_id}

HARD REJECT — set transportable=false if ANY of these apply:
- Criticizes the Chinese government, CCP, or Chinese leaders (instant Bilibili ban)
- Frames China's military as weak, incompetent, or inferior (audience will mass-report)
- Antisemitic, racist, or promotes hatred against any ethnic/religious group
- Involves Taiwan/Tibet/Xinjiang independence or sovereignty disputes
- Promotes religious extremism or cult content
- Contains graphic violence, gore, or sexual content
- Is primarily about Indian domestic politics or Indian nationalism (poor audience fit)

BILIBILI CONTENT POLICY awareness:
- No criticism of Chinese government or leadership
- No content that could be read as "foreign media attacking China"
- Military content is OK only if it's neutral analysis or favorable to China
- Geopolitics is OK only if the take doesn't antagonize Chinese viewers

Persona fit — our channel persona is a sarcastic 吐槽AI:
- "Controversy" means ENTERTAINMENT controversy: product failures, corporate greed,
  absurd challenges, consumer ripoffs, industry drama, workplace nonsense.
- NOT political/religious/ethnic controversy — that gets accounts banned.
- Good fit: tech roasts, epic fails, consumer exposés, absurd experiments, gaming drama
- Bad fit: religious debates, ethnic tensions, military propaganda, political commentary

Rate persona_fit 0.0-1.0 (how well this video works for sarcastic entertainment commentary).

Respond with JSON:
{{"transportable": <bool>, "persona_fit": <float 0.0-1.0>, "reasoning": "<1 sentence>"}}"""


def check_transportability(
    backend,
    title: str,
    channel: str,
    duration_seconds: int,
    category_id: int,
) -> dict:
    """Check if a YouTube video is suitable for transport to Bilibili.

    Uses a two-layer approach:
    1. Hard keyword filter (pre-LLM) for obviously dangerous content
    2. LLM judgment for nuanced cases

    Args:
        backend: LLMBackend instance.
        title: YouTube video title.
        channel: YouTube channel name.
        duration_seconds: Video duration in seconds.
        category_id: YouTube category ID.

    Returns:
        Dict with 'transportable' (bool), 'persona_fit' (float), 'reasoning' (str).
    """
    # Layer 1: Hard keyword filter — no LLM call needed
    block_reason = _check_content_safety(title)
    if block_reason:
        logger.info("Content safety block: '%s' — %s", title[:60], block_reason)
        return {
            "transportable": False,
            "persona_fit": 0.0,
            "reasoning": f"BLOCKED: {block_reason}",
        }

    # Layer 2: LLM judgment
    prompt = TRANSPORTABILITY_PROMPT.format(
        title=title,
        channel=channel,
        duration_minutes=duration_seconds / 60.0,
        category_id=category_id,
    )

    try:
        response = backend.chat(
            messages=[
                {"role": "system", "content": "You assess video transportability. Respond in JSON."},
                {"role": "user", "content": prompt},
            ],
            json_schema={"type": "object", "properties": {
                "transportable": {"type": "boolean"},
                "persona_fit": {"type": "number"},
                "reasoning": {"type": "string"},
            }},
        )
        result = json.loads(response)
        transportable = result.get("transportable", True)
        persona_fit = float(result.get("persona_fit", 0.5))
        reasoning = result.get("reasoning", "")

        if persona_fit < 0.3 and transportable:
            transportable = False
            reasoning = f"Low persona fit ({persona_fit:.1f}): {reasoning}"

        return {
            "transportable": transportable,
            "persona_fit": persona_fit,
            "reasoning": reasoning,
        }
    except Exception as e:
        logger.warning("Transportability check failed: %s", e)
        return {"transportable": True, "persona_fit": 0.5, "reasoning": f"Check failed: {e}"}
