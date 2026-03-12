"""
Upload CC subtitles to Bilibili via their subtitle API.

Workflow:
1. Parse biliup's cookies.json for SESSDATA and bili_jct (CSRF token)
2. Get the CID for a given BV video ID
3. Convert SRT subtitle to Bilibili's BCC JSON format
4. Upload via /x/v2/dm/subtitle/draft/save
"""
import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BILIBILI_API = "https://api.bilibili.com"


def load_cookies(path: str | Path) -> dict[str, str]:
    """Parse biliup's cookies.json and return SESSDATA + bili_jct.

    Args:
        path: Path to biliup cookies.json file.

    Returns:
        Dict with 'SESSDATA' and 'bili_jct' keys.

    Raises:
        FileNotFoundError: If cookies file doesn't exist.
        KeyError: If required cookies are missing.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cookies = {c["name"]: c["value"] for c in data["cookie_info"]["cookies"]}
    for key in ("SESSDATA", "bili_jct"):
        if key not in cookies:
            raise KeyError(f"Missing '{key}' in cookies.json")
    return {"SESSDATA": cookies["SESSDATA"], "bili_jct": cookies["bili_jct"]}


def get_cid(bvid: str, client: httpx.Client) -> int:
    """Get the CID (cid of the first part) for a Bilibili video.

    Args:
        bvid: Bilibili video ID (e.g. BV1xx411x7xx).
        client: httpx client with cookies set.

    Returns:
        The CID integer.

    Raises:
        ValueError: If the API response is unexpected.
    """
    resp = client.get(
        f"{BILIBILI_API}/x/web-interface/view",
        params={"bvid": bvid},
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise ValueError(f"Bilibili API error: {data.get('message', data)}")
    pages = data["data"]["pages"]
    if not pages:
        raise ValueError(f"No pages found for {bvid}")
    return pages[0]["cid"]


def _parse_srt_timestamp(ts: str) -> float:
    """Convert SRT timestamp (HH:MM:SS,mmm) to seconds."""
    ts = ts.strip().replace(",", ".")
    parts = ts.split(":")
    h, m = int(parts[0]), int(parts[1])
    s = float(parts[2])
    return h * 3600 + m * 60 + s


def srt_to_bcc(srt_text: str) -> dict[str, Any]:
    """Convert SRT subtitle text to Bilibili BCC JSON format.

    Args:
        srt_text: Contents of an SRT subtitle file.

    Returns:
        BCC-formatted dict ready for JSON serialization.
    """
    # Parse SRT blocks: index, timestamp line, content lines
    blocks = re.split(r"\n\s*\n", srt_text.strip())
    body = []

    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue

        # Line 1: index (skip)
        # Line 2: timestamps
        ts_match = re.match(
            r"(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})",
            lines[1],
        )
        if not ts_match:
            continue

        start = _parse_srt_timestamp(ts_match.group(1))
        end = _parse_srt_timestamp(ts_match.group(2))
        # Content is everything after the timestamp line
        content = " ".join(lines[2:]).strip()
        # Strip HTML tags that auto-subs sometimes include
        content = re.sub(r"<[^>]+>", "", content)
        if not content:
            continue

        body.append({
            "from": round(start, 3),
            "to": round(end, 3),
            "location": 2,
            "content": content,
        })

    return {
        "font_size": 0.4,
        "font_color": "#FFFFFF",
        "background_alpha": 0.5,
        "background_color": "#9C27B0",
        "Stroke": "none",
        "body": body,
    }


def upload_subtitle(
    bvid: str,
    srt_path: str | Path,
    cookies_path: str | Path,
) -> bool:
    """Upload an SRT subtitle file as CC to a Bilibili video.

    Args:
        bvid: Bilibili video ID.
        srt_path: Path to the .srt file.
        cookies_path: Path to biliup's cookies.json.

    Returns:
        True on success, False on failure.
    """
    try:
        creds = load_cookies(cookies_path)
    except (FileNotFoundError, KeyError, json.JSONDecodeError) as e:
        logger.error("Failed to load cookies: %s", e)
        return False

    srt_text = Path(srt_path).read_text(encoding="utf-8")
    bcc = srt_to_bcc(srt_text)

    if not bcc["body"]:
        logger.warning("SRT file has no usable subtitle entries: %s", srt_path)
        return False

    with httpx.Client(
        cookies={"SESSDATA": creds["SESSDATA"]},
        headers={"Referer": "https://www.bilibili.com"},
        timeout=30,
    ) as client:
        try:
            cid = get_cid(bvid, client)
        except (httpx.HTTPError, ValueError) as e:
            logger.error("Failed to get CID for %s: %s", bvid, e)
            return False

        # Submit subtitle
        resp = client.post(
            f"{BILIBILI_API}/x/v2/dm/subtitle/draft/save",
            data={
                "type": 1,
                "oid": cid,
                "lan": "zh-Hans",
                "bvid": bvid,
                "submit": True,
                "sign": False,
                "csrf": creds["bili_jct"],
                "data": json.dumps(bcc, ensure_ascii=False),
            },
        )
        resp.raise_for_status()
        result = resp.json()

        if result.get("code") == 0:
            logger.info("Subtitle uploaded for %s (cid=%d)", bvid, cid)
            return True
        else:
            logger.error(
                "Subtitle upload failed for %s: %s",
                bvid,
                result.get("message", result),
            )
            return False
