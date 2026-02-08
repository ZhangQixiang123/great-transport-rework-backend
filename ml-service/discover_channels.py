"""
Discover independent YouTube transporter channels on Bilibili.

Searches for videos with transporter keywords, extracts uploader UIDs,
fetches channel info, and ranks by video count for training data collection.
"""
import asyncio
import time
from collections import defaultdict
from bilibili_api import search, user
from bilibili_api.search import SearchObjectType, OrderVideo

# Keywords that indicate YouTube-transported content
KEYWORDS = [
    "搬运 youtube",
    "油管搬运",
    "转载 youtube",
    "youtube 中文字幕",
    "油管 中文字幕",
    "搬运 油管",
    "youtube搬运",
]

# Known official/authorized channels to exclude
OFFICIAL_UIDS = {
    "12434430",   # LinusTechTips (辉光字幕组)
    "12564758",   # TechQuickie
    "693340454",  # 科技过电
    "2008843652", # Mac Address
    "145716",     # FPS罗兹 (ElectroBOOM)
    "88461692",   # 3Blue1Brown
    "94742590",   # Veritasium
    "1027737427", # MrBeast
    "9458053",    # 李永乐
    "221648",     # 柚子木字幕组 (subtitle group, not independent)
}

# Rate limiting
MIN_INTERVAL = 1.0
last_request = 0.0


async def rate_limit():
    global last_request
    now = time.time()
    wait = MIN_INTERVAL - (now - last_request)
    if wait > 0:
        await asyncio.sleep(wait)
    last_request = time.time()


async def search_videos(keyword: str, pages: int = 3) -> list:
    """Search for videos with a keyword, return list of (uid, author) tuples."""
    results = []
    for page in range(1, pages + 1):
        await rate_limit()
        try:
            resp = await search.search_by_type(
                keyword=keyword,
                search_type=SearchObjectType.VIDEO,
                order_type=OrderVideo.TOTALRANK,
                page=page,
                page_size=42,
            )
            video_list = resp.get("result", [])
            if not video_list:
                break
            for v in video_list:
                mid = str(v.get("mid", ""))
                author = v.get("author", "")
                title = v.get("title", "")
                if mid and mid not in OFFICIAL_UIDS:
                    results.append((mid, author, title))
        except Exception as e:
            print(f"  Error searching '{keyword}' page {page}: {e}")
            break
    return results


async def get_channel_info(uid: str) -> dict | None:
    """Get channel info for a UID."""
    await rate_limit()
    try:
        u = user.User(uid=int(uid))
        info = await u.get_user_info()
        return {
            "uid": uid,
            "name": info.get("name", ""),
            "sign": info.get("sign", ""),
            "followers": info.get("fans", 0),
            "level": info.get("level", 0),
            "official": info.get("official", {}).get("type", -1),
            # official type: 0=verified org, 1=verified person, -1=none
        }
    except Exception as e:
        print(f"  Error getting info for {uid}: {e}")
        return None


async def get_video_count(uid: str) -> int:
    """Get total video count for a channel."""
    await rate_limit()
    try:
        u = user.User(uid=int(uid))
        resp = await u.get_videos(pn=1, ps=1)
        if resp and "page" in resp:
            return resp["page"].get("count", 0)
        return 0
    except Exception as e:
        return 0


async def main():
    print("=" * 70)
    print("Bilibili YouTube Transporter Channel Discovery")
    print("=" * 70)

    # Phase 1: Search for videos and collect UIDs
    uid_hits = defaultdict(lambda: {"count": 0, "author": "", "sample_titles": []})

    for keyword in KEYWORDS:
        print(f"\nSearching: '{keyword}'...")
        results = await search_videos(keyword, pages=3)
        print(f"  Found {len(results)} video results")

        for mid, author, title in results:
            uid_hits[mid]["count"] += 1
            uid_hits[mid]["author"] = author
            if len(uid_hits[mid]["sample_titles"]) < 3:
                # Strip HTML tags from title
                import re
                clean_title = re.sub(r'<.*?>', '', title)
                uid_hits[mid]["sample_titles"].append(clean_title)

    print(f"\n{'=' * 70}")
    print(f"Found {len(uid_hits)} unique channels from search results")

    # Phase 2: Sort by frequency (channels that appear in multiple searches)
    sorted_uids = sorted(uid_hits.items(), key=lambda x: x[1]["count"], reverse=True)

    # Phase 3: Get detailed info for top candidates (top 40)
    top_candidates = sorted_uids[:40]
    print(f"\nFetching details for top {len(top_candidates)} channels...")

    channels = []
    for uid, hit_data in top_candidates:
        info = await get_channel_info(uid)
        if info is None:
            continue

        # Skip verified organizations (official type 0)
        if info["official"] == 0:
            print(f"  Skipping verified org: {info['name']} ({uid})")
            continue

        video_count = await get_video_count(uid)

        channels.append({
            **info,
            "video_count": video_count,
            "search_hits": hit_data["count"],
            "sample_titles": hit_data["sample_titles"],
        })
        print(f"  {info['name']} (UID:{uid}) - {video_count} videos, {info['followers']} followers")

    # Phase 4: Sort by video count and display results
    channels.sort(key=lambda x: x["video_count"], reverse=True)

    print(f"\n{'=' * 70}")
    print("DISCOVERY RESULTS - Independent YouTube Transporter Channels")
    print(f"{'=' * 70}")
    print(f"{'Rank':<5} {'Name':<25} {'UID':<15} {'Videos':<8} {'Followers':<12} {'Hits':<5}")
    print("-" * 70)

    for i, ch in enumerate(channels, 1):
        name = ch["name"][:24]
        print(f"{i:<5} {name:<25} {ch['uid']:<15} {ch['video_count']:<8} {ch['followers']:<12} {ch['search_hits']:<5}")

    # Phase 5: Category breakdown
    print(f"\n{'=' * 70}")
    print("DETAILED CHANNEL INFO")
    print(f"{'=' * 70}")

    for i, ch in enumerate(channels[:20], 1):
        print(f"\n{i}. {ch['name']} (UID: {ch['uid']})")
        print(f"   Videos: {ch['video_count']} | Followers: {ch['followers']} | Level: {ch['level']}")
        print(f"   Bio: {ch['sign'][:80]}")
        if ch["sample_titles"]:
            print(f"   Sample titles:")
            for t in ch["sample_titles"]:
                print(f"     - {t[:70]}")

    # Summary stats
    total_videos = sum(ch["video_count"] for ch in channels)
    print(f"\n{'=' * 70}")
    print(f"SUMMARY: {len(channels)} independent channels, {total_videos} total videos available")
    print(f"{'=' * 70}")

    # Output UIDs for easy copy-paste
    print("\nUIDs for collection (sorted by video count):")
    for ch in channels:
        if ch["video_count"] >= 20:  # Only channels with 20+ videos
            print(f"  {ch['uid']}  # {ch['name']} ({ch['video_count']} videos)")


if __name__ == "__main__":
    import sys
    import io
    # Write results to file to avoid terminal encoding issues
    with open("discovery_results.txt", "w", encoding="utf-8") as f:
        old_stdout = sys.stdout
        sys.stdout = f
        asyncio.run(main())
        sys.stdout = old_stdout
    print("Results written to discovery_results.txt")
