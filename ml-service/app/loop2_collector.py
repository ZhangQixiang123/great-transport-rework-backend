"""Loop 2 Data Collector — fetches Bilibili view counts for transported videos.

Pulls completed bvids from Go backend, queries public Bilibili API for stats,
writes back to strategy_runs via OutcomeTracker.
"""
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BILIBILI_VIEW_API = "https://api.bilibili.com/x/web-interface/view"
_BILIBILI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
}


def collect_loop2_data(
    db,
    go_url: str = "http://localhost:8081",
    rate_limit: float = 0.5,
) -> dict[str, Any]:
    """Fetch Bilibili stats for all transported videos and update outcomes.

    Returns: {collected: int, updated: int, errors: int, details: [...]}
    """
    from app.personas._shared.outcomes import OutcomeTracker

    tracker = OutcomeTracker(db)

    # 1. Get completed jobs with bvids from Go backend
    try:
        resp = httpx.get(f"{go_url}/upload/jobs?limit=200", timeout=10)
        resp.raise_for_status()
        jobs = resp.json()
    except Exception as e:
        logger.error("Failed to fetch jobs from Go: %s", e)
        return {"collected": 0, "updated": 0, "errors": 1, "details": [str(e)]}

    completed = [j for j in jobs if j.get("status") == "completed" and j.get("bilibili_bvid")]
    if not completed:
        return {"collected": 0, "updated": 0, "errors": 0, "details": ["No completed jobs with bvids"]}

    # 2. Check which bvids already have outcome data
    already_recorded = set()
    if db._conn:
        rows = db._conn.execute("""
            SELECT bilibili_bvid FROM strategy_runs
            WHERE bilibili_bvid IS NOT NULL AND bilibili_views IS NOT NULL
        """).fetchall()
        already_recorded = {r["bilibili_bvid"] for r in rows}

    collected = 0
    updated = 0
    errors = 0
    details = []

    for job in completed:
        bvid = job["bilibili_bvid"]
        video_id = job["video_id"]
        title = job.get("title", "")
        strategy_name = job.get("strategy_name", "")

        # Mark as transported (links strategy_run to bvid)
        tracker.mark_transported(video_id, bvid)

        # If Go provides strategy_name, directly associate with strategy_runs
        if strategy_name and db._conn:
            try:
                db._conn.execute("""
                    UPDATE strategy_runs
                    SET was_transported = 1, bilibili_bvid = ?
                    WHERE youtube_video_id = ?
                      AND bilibili_bvid IS NULL
                      AND id IN (
                          SELECT sr.id FROM strategy_runs sr
                          JOIN strategies s ON sr.strategy_id = s.id
                          WHERE s.name = ?
                          ORDER BY sr.id DESC LIMIT 1
                      )
                """, (bvid, video_id, strategy_name))
                db._conn.commit()
            except Exception as e:
                logger.warning("Loop2: direct strategy association failed for %s: %s", bvid, e)

        if bvid in already_recorded:
            continue

        # 3. Fetch Bilibili stats
        try:
            resp = httpx.get(BILIBILI_VIEW_API, params={"bvid": bvid}, headers=_BILIBILI_HEADERS, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != 0:
                details.append(f"{bvid}: API error {data.get('message', 'unknown')}")
                errors += 1
                continue

            views = data["data"]["stat"]["view"]
            tracker.update_bilibili_views(bvid, views)
            collected += 1

            outcome = "success" if views >= 50000 else "failure"
            details.append(f"{bvid} ({title[:30]}): {views:,} views [{outcome}]")
            logger.info("Loop2: %s -> %d views [%s]", bvid, views, outcome)

        except Exception as e:
            errors += 1
            details.append(f"{bvid}: fetch error {e}")
            logger.warning("Loop2: failed to fetch %s: %s", bvid, e)

        time.sleep(rate_limit)

    # Also update already-recorded bvids with fresh view counts
    for job in completed:
        bvid = job["bilibili_bvid"]
        if bvid not in already_recorded:
            continue

        try:
            resp = httpx.get(BILIBILI_VIEW_API, params={"bvid": bvid}, headers=_BILIBILI_HEADERS, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") == 0:
                views = data["data"]["stat"]["view"]
                tracker.update_bilibili_views(bvid, views)
                updated += 1
        except Exception:
            pass

        time.sleep(rate_limit)

    return {"collected": collected, "updated": updated, "errors": errors, "details": details}
