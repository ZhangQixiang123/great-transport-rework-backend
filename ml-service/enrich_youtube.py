"""
Enrich competitor videos with YouTube original stats.

Phase 1: Fetch stats for 247 videos with known youtube_source_id (cheap: ~5 API units)
Phase 2: Title-match remaining videos via YouTube search (100 units per search, budget ~100/day)
"""
import json
import sqlite3
import sys
import time
from datetime import datetime

import httpx

YOUTUBE_API_KEY = "AIzaSyAvCrdRnFYXwya6MIEdcN9jv4V-SxFYu1U"
DB_PATH = "data.db"
BATCH_SIZE = 50  # YouTube API allows up to 50 IDs per request


def ensure_youtube_table(conn: sqlite3.Connection):
    """Create youtube_stats table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS youtube_stats (
            youtube_id TEXT PRIMARY KEY,
            bvid TEXT,
            yt_title TEXT,
            yt_channel_title TEXT,
            yt_views INTEGER DEFAULT 0,
            yt_likes INTEGER DEFAULT 0,
            yt_comments INTEGER DEFAULT 0,
            yt_duration_seconds INTEGER DEFAULT 0,
            yt_published_at TEXT,
            yt_category_id INTEGER,
            yt_tags TEXT,
            match_method TEXT DEFAULT 'source_id',
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_youtube_stats_bvid
        ON youtube_stats(bvid)
    """)
    conn.commit()


def parse_duration(duration_str: str) -> int:
    """Parse ISO 8601 duration (PT1H2M3S) to seconds."""
    import re
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str or '')
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def fetch_video_stats(client: httpx.Client, video_ids: list[str]) -> dict:
    """Fetch stats for a batch of YouTube video IDs (max 50)."""
    resp = client.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(video_ids),
            "key": YOUTUBE_API_KEY,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    results = {}
    for item in data.get("items", []):
        vid = item["id"]
        snippet = item.get("snippet", {})
        stats = item.get("statistics", {})
        content = item.get("contentDetails", {})

        results[vid] = {
            "yt_title": snippet.get("title", ""),
            "yt_channel_title": snippet.get("channelTitle", ""),
            "yt_published_at": snippet.get("publishedAt", ""),
            "yt_category_id": int(snippet.get("categoryId", 0)),
            "yt_tags": json.dumps(snippet.get("tags", []), ensure_ascii=False),
            "yt_views": int(stats.get("viewCount", 0)),
            "yt_likes": int(stats.get("likeCount", 0)),
            "yt_comments": int(stats.get("commentCount", 0)),
            "yt_duration_seconds": parse_duration(content.get("duration", "")),
        }
    return results


def search_youtube_by_title(client: httpx.Client, title: str) -> str | None:
    """Search YouTube for a video by title. Returns video ID if found. Costs 100 quota units."""
    # Clean title: remove common Bilibili additions
    import re
    clean = re.sub(r'[\[【].*?[\]】]', '', title).strip()
    clean = re.sub(r'#\S+', '', clean).strip()
    if len(clean) < 5:
        return None

    resp = client.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={
            "part": "snippet",
            "q": clean,
            "type": "video",
            "maxResults": 1,
            "key": YOUTUBE_API_KEY,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    items = data.get("items", [])
    if items:
        return items[0]["id"]["videoId"]
    return None


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    ensure_youtube_table(conn)

    client = httpx.Client()

    # ================================================================
    # PHASE 1: Fetch stats for videos with known youtube_source_id
    # ================================================================
    print("=" * 70)
    print("PHASE 1: Fetching stats for videos with known YouTube IDs")
    print("=" * 70)

    rows = conn.execute("""
        SELECT bvid, youtube_source_id
        FROM competitor_videos
        WHERE youtube_source_id IS NOT NULL AND youtube_source_id != ''
    """).fetchall()

    # Filter out already-fetched
    existing = set(r[0] for r in conn.execute("SELECT youtube_id FROM youtube_stats").fetchall())
    to_fetch = [(r["bvid"], r["youtube_source_id"]) for r in rows
                if r["youtube_source_id"] not in existing]

    print(f"  Total with youtube_source_id: {len(rows)}")
    print(f"  Already fetched: {len(existing)}")
    print(f"  To fetch: {len(to_fetch)}")

    # Build mapping: youtube_id -> bvid
    yt_to_bvid = {yt_id: bvid for bvid, yt_id in to_fetch}
    yt_ids = list(yt_to_bvid.keys())

    fetched = 0
    not_found = 0
    for i in range(0, len(yt_ids), BATCH_SIZE):
        batch = yt_ids[i:i + BATCH_SIZE]
        print(f"  Fetching batch {i // BATCH_SIZE + 1} ({len(batch)} IDs)...")

        try:
            stats = fetch_video_stats(client, batch)

            for yt_id in batch:
                if yt_id in stats:
                    s = stats[yt_id]
                    conn.execute("""
                        INSERT OR REPLACE INTO youtube_stats
                        (youtube_id, bvid, yt_title, yt_channel_title, yt_views, yt_likes,
                         yt_comments, yt_duration_seconds, yt_published_at, yt_category_id,
                         yt_tags, match_method, fetched_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'source_id', ?)
                    """, (
                        yt_id, yt_to_bvid[yt_id],
                        s["yt_title"], s["yt_channel_title"],
                        s["yt_views"], s["yt_likes"], s["yt_comments"],
                        s["yt_duration_seconds"], s["yt_published_at"],
                        s["yt_category_id"], s["yt_tags"],
                        datetime.utcnow().isoformat(),
                    ))
                    fetched += 1
                else:
                    not_found += 1

            conn.commit()
        except Exception as e:
            print(f"  ERROR: {e}")
            break

        time.sleep(0.5)

    print(f"\n  Phase 1 results: {fetched} fetched, {not_found} not found (deleted/private)")

    # ================================================================
    # PHASE 2: Title-match for videos WITHOUT youtube_source_id
    # ================================================================
    print()
    print("=" * 70)
    print("PHASE 2: Title-matching remaining videos via YouTube search")
    print("=" * 70)

    # Get videos without youtube source that haven't been matched yet
    matched_bvids = set(r[0] for r in conn.execute("SELECT bvid FROM youtube_stats").fetchall())
    unmatched = conn.execute("""
        SELECT bvid, title, views
        FROM competitor_videos
        WHERE (youtube_source_id IS NULL OR youtube_source_id = '')
        ORDER BY views DESC
    """).fetchall()
    unmatched = [r for r in unmatched if r["bvid"] not in matched_bvids]

    print(f"  Videos without YouTube ID: {len(unmatched)}")

    # Budget: ~95 searches (save 5 units buffer from 10K daily quota)
    # Prioritize by views (higher view videos = more valuable training data)
    SEARCH_BUDGET = 95
    candidates = unmatched[:SEARCH_BUDGET]
    print(f"  Will search for top {len(candidates)} by views (budget: {SEARCH_BUDGET} searches = {SEARCH_BUDGET * 100} quota units)")

    search_matched = 0
    search_failed = 0
    search_errors = 0

    for idx, row in enumerate(candidates):
        bvid = row["bvid"]
        title = row["title"]
        print(f"  [{idx + 1}/{len(candidates)}] Searching: {title[:60]}...", end=" ")

        try:
            yt_id = search_youtube_by_title(client, title)

            if yt_id:
                # Fetch full stats for the matched video
                stats = fetch_video_stats(client, [yt_id])
                if yt_id in stats:
                    s = stats[yt_id]
                    conn.execute("""
                        INSERT OR REPLACE INTO youtube_stats
                        (youtube_id, bvid, yt_title, yt_channel_title, yt_views, yt_likes,
                         yt_comments, yt_duration_seconds, yt_published_at, yt_category_id,
                         yt_tags, match_method, fetched_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'title_search', ?)
                    """, (
                        yt_id, bvid,
                        s["yt_title"], s["yt_channel_title"],
                        s["yt_views"], s["yt_likes"], s["yt_comments"],
                        s["yt_duration_seconds"], s["yt_published_at"],
                        s["yt_category_id"], s["yt_tags"],
                        datetime.utcnow().isoformat(),
                    ))
                    conn.commit()

                    # Also update the competitor_videos table
                    conn.execute("""
                        UPDATE competitor_videos SET youtube_source_id = ? WHERE bvid = ?
                    """, (yt_id, bvid))
                    conn.commit()

                    search_matched += 1
                    print(f"MATCHED -> {s['yt_title'][:40]} ({s['yt_views']:,} views)")
                else:
                    search_failed += 1
                    print("found ID but no stats")
            else:
                search_failed += 1
                print("no match")

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                print(f"\n  QUOTA EXCEEDED — stopping searches")
                break
            search_errors += 1
            print(f"ERROR: {e}")
        except Exception as e:
            search_errors += 1
            print(f"ERROR: {e}")

        time.sleep(1)  # Be nice to the API

    print(f"\n  Phase 2 results: {search_matched} matched, {search_failed} no match, {search_errors} errors")

    # ================================================================
    # SUMMARY
    # ================================================================
    print()
    print("=" * 70)
    print("ENRICHMENT SUMMARY")
    print("=" * 70)

    total_yt = conn.execute("SELECT COUNT(*) FROM youtube_stats").fetchone()[0]
    by_method = conn.execute("""
        SELECT match_method, COUNT(*) as cnt
        FROM youtube_stats
        GROUP BY match_method
    """).fetchall()

    print(f"  Total videos with YouTube stats: {total_yt}")
    for r in by_method:
        print(f"    {r['match_method']}: {r['cnt']}")

    # Show YouTube stats overview
    yt_stats = conn.execute("""
        SELECT yt_views, yt_likes, yt_comments, yt_channel_title
        FROM youtube_stats
        WHERE yt_views > 0
    """).fetchall()

    if yt_stats:
        import numpy as np
        yt_views = np.array([r["yt_views"] for r in yt_stats])
        print(f"\n  YouTube views distribution (of matched videos):")
        for p in [10, 25, 50, 75, 90, 95]:
            print(f"    P{p}: {np.percentile(yt_views, p):>12,.0f}")
        print(f"    Mean: {np.mean(yt_views):>12,.0f}")

    # Show correlation preview
    joined = conn.execute("""
        SELECT cv.views as bili_views, ys.yt_views
        FROM competitor_videos cv
        JOIN youtube_stats ys ON cv.bvid = ys.bvid
        WHERE ys.yt_views > 0 AND cv.views > 0
    """).fetchall()

    if len(joined) >= 10:
        import numpy as np
        bili = np.log1p(np.array([r["bili_views"] for r in joined]))
        yt = np.log1p(np.array([r["yt_views"] for r in joined]))
        corr = np.corrcoef(bili, yt)[0, 1]
        print(f"\n  Correlation: log(YouTube views) vs log(Bilibili views): r = {corr:+.4f}")
        print(f"  (based on {len(joined)} matched videos)")

    total_videos = conn.execute("SELECT COUNT(*) FROM competitor_videos").fetchone()[0]
    coverage = total_yt / total_videos * 100 if total_videos > 0 else 0
    print(f"\n  Coverage: {total_yt}/{total_videos} videos ({coverage:.1f}%)")

    client.close()
    conn.close()


if __name__ == "__main__":
    main()
