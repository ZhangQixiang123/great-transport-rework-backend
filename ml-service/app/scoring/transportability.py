"""LLM transportability check — fixed prompt, not a skill (yet)."""

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

TRANSPORTABILITY_PROMPT = """Is this YouTube video suitable for transport to Bilibili?

Title: {title}
Channel: {channel}
Duration: {duration_minutes:.1f} minutes
Category ID: {category_id}

Consider:
- Is the content mostly visual or heavily language-dependent?
- Any cultural sensitivity issues for Chinese audiences?
- Would this appeal to Bilibili's demographic (18-30, urban, educated)?

Respond with JSON:
{{"transportable": <bool>, "reasoning": "<1 sentence>"}}"""


def check_transportability(
    backend,
    title: str,
    channel: str,
    duration_seconds: int,
    category_id: int,
) -> dict:
    """Check if a YouTube video is suitable for transport to Bilibili.

    Args:
        backend: LLMBackend instance.
        title: YouTube video title.
        channel: YouTube channel name.
        duration_seconds: Video duration in seconds.
        category_id: YouTube category ID.

    Returns:
        Dict with 'transportable' (bool) and 'reasoning' (str).
    """
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
                "reasoning": {"type": "string"},
            }},
        )
        result = json.loads(response)
        return {
            "transportable": result.get("transportable", True),
            "reasoning": result.get("reasoning", ""),
        }
    except Exception as e:
        logger.warning("Transportability check failed: %s", e)
        return {"transportable": True, "reasoning": f"Check failed: {e}"}
