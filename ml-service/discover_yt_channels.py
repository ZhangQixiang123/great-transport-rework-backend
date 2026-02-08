"""
Find Bilibili transporter channels that consistently reference YouTube source IDs.
Search for transported content from popular YouTube creators, then check
which Bilibili channels have high YouTube ID detection rates.
"""
import asyncio
import json
import re
import sys
import time
from collections import defaultdict

sys.path.insert(0, ".")

from bilibili_api import search, user
from bilibili_api.search import SearchObjectType, OrderVideo

# YouTube creators whose content is commonly transported
YOUTUBE_CREATORS = [
    "pewdiepie",
    "MrBeast",
    "Mark Rober",
    "Dude Perfect",
    "Kurzgesagt",
    "Veritasium",
    "Tom Scott",
    "SmarterEveryDay",
    "Corridor Crew",
    "JCS Criminal Psychology",
    "Vsauce",
    "Numberphile",
    "CGP Grey",
    "Lemmino",
    "Wired",
    "Vox",
    "Johnny Harris",
    "Real Engineering",
    "Wendover Productions",
    "Half as Interesting",
    "NileRed",
    "StuffMadeHere",
    "Michael Reeves",
    "William Osman",
    "penguinz0",
    "Daily Dose Of Internet",
    "Binging with Babish",
    "Gordon Ramsay",
    "Jacksepticeye",
    "Markiplier",
]

# Also search with Chinese keywords that suggest YouTube ID inclusion
PATTERN_KEYWORDS = [
    "youtube.com/watch",
    "youtu.be",
    "[搬运] youtube",
    "原链接 youtube",
    "source youtube",
]

# YouTube ID patterns (same as in competitor_monitor.py)
YOUTUBE_ID_PATTERNS = [
    r'\[([a-zA-Z0-9_-]{11})\]',
    r'\(source:\s*([a-zA-Z0-9_-]{11})\)',
    r'youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
    r'youtu\.be/([a-zA-Z0-9_-]{11})',
    r'yt:\s*([a-zA-Z0-9_-]{11})',
    r'YouTube:\s*([a-zA-Z0-9_-]{11})',
    r'source=([a-zA-Z0-9_-]{11})',
    r'Original:\s*([a-zA-Z0-9_-]{11})',
]

# Already tracked channels
EXISTING_UIDS = {
    "375797865", "4390568", "544112908", "677245745", "662792114",
    "325069537", "3546858064448344", "17198256", "1121309703", "50944796",
    # Official channels
    "12434430", "12564758", "693340454", "2008843652", "145716",
    "88461692", "94742590", "1027737427", "9458053", "221648",
}

MIN_INTERVAL = 1.2
last_request = 0.0


async def rate_limit():
    global last_request
    now = time.time()
    wait = MIN_INTERVAL - (now - last_request)
    if wait > 0:
        await asyncio.sleep(wait)
    last_request = time.time()


def has_youtube_id(title: str, description: str) -> bool:
    """Check if title or description contains a YouTube video ID."""
    text = f"{title} {description}"
    for pattern in YOUTUBE_ID_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


async def search_videos(keyword: str, pages: int = 2) -> list:
    """Search Bilibili for videos with keyword."""
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
            for v in resp.get("result", []):
                mid = str(v.get("mid", ""))
                if mid and mid not in EXISTING_UIDS:
                    # Strip HTML tags
                    title = re.sub(r'<.*?>', '', v.get("title", ""))
                    desc = v.get("description", "") or ""
                    results.append({
                        "mid": mid,
                        "author": v.get("author", ""),
                        "title": title,
                        "description": desc,
                        "bvid": v.get("bvid", ""),
                    })
        except Exception as e:
            print(f"  Error: {e}")
            break
    return results


async def check_channel_yt_rate(uid: str, sample_size: int = 30) -> dict:
    """Check what % of a channel's videos have YouTube source IDs."""
    await rate_limit()
    try:
        u = user.User(uid=int(uid))
        resp = await u.get_videos(pn=1, ps=min(sample_size, 50))
        if not resp:
            return {"uid": uid, "total": 0, "with_yt_id": 0, "rate": 0}

        vlist = resp.get("list", {}).get("vlist", [])
        total = len(vlist)
        with_yt = 0

        for v in vlist:
            title = v.get("title", "")
            desc = v.get("description", "") or ""
            if has_youtube_id(title, desc):
                with_yt += 1

        video_count = resp.get("page", {}).get("count", total)

        return {
            "uid": uid,
            "total_sampled": total,
            "total_videos": video_count,
            "with_yt_id": with_yt,
            "rate": with_yt / total if total > 0 else 0,
        }
    except Exception as e:
        return {"uid": uid, "total_sampled": 0, "error": str(e)}


async def get_channel_name(uid: str) -> str:
    await rate_limit()
    try:
        u = user.User(uid=int(uid))
        info = await u.get_user_info()
        return info.get("name", uid)
    except:
        return uid


async def main():
    print("=" * 70)
    print("Discovering channels with high YouTube ID reference rates")
    print("=" * 70)

    # Collect candidate channels
    channel_hits = defaultdict(lambda: {"count": 0, "author": "", "yt_ids_in_search": 0})

    # Search by YouTube creator names
    print("\nSearching by YouTube creator names...")
    for creator in YOUTUBE_CREATORS:
        keyword = f"{creator} 搬运"
        print(f"  Searching: '{keyword}'...", end=" ")
        results = await search_videos(keyword, pages=1)
        print(f"{len(results)} results")

        for r in results:
            mid = r["mid"]
            channel_hits[mid]["count"] += 1
            channel_hits[mid]["author"] = r["author"]
            if has_youtube_id(r["title"], r["description"]):
                channel_hits[mid]["yt_ids_in_search"] += 1

    # Search by pattern keywords
    print("\nSearching by pattern keywords...")
    for keyword in PATTERN_KEYWORDS:
        print(f"  Searching: '{keyword}'...", end=" ")
        results = await search_videos(keyword, pages=2)
        print(f"{len(results)} results")

        for r in results:
            mid = r["mid"]
            channel_hits[mid]["count"] += 1
            channel_hits[mid]["author"] = r["author"]
            if has_youtube_id(r["title"], r["description"]):
                channel_hits[mid]["yt_ids_in_search"] += 1

    print(f"\nFound {len(channel_hits)} candidate channels")

    # Sort by yt_ids_in_search (channels whose search results already have YT IDs)
    sorted_channels = sorted(
        channel_hits.items(),
        key=lambda x: (x[1]["yt_ids_in_search"], x[1]["count"]),
        reverse=True,
    )

    # Check top 30 candidates for YouTube ID rate
    top_candidates = sorted_channels[:30]
    print(f"\nChecking YouTube ID rates for top {len(top_candidates)} candidates...")

    good_channels = []
    for uid, data in top_candidates:
        result = check_result = await check_channel_yt_rate(uid, sample_size=30)
        name = await get_channel_name(uid)

        rate = check_result.get("rate", 0)
        total_videos = check_result.get("total_videos", 0)
        with_yt = check_result.get("with_yt_id", 0)
        sampled = check_result.get("total_sampled", 0)

        if rate > 0.3 and total_videos >= 20:
            good_channels.append({
                "uid": uid,
                "name": name,
                "total_videos": total_videos,
                "yt_rate": rate,
                "yt_in_sample": with_yt,
                "sampled": sampled,
                "search_hits": data["count"],
            })
            print(f"  GOOD: {name} ({uid}) - {total_videos} videos, {rate:.0%} YT ID rate ({with_yt}/{sampled})")
        elif rate > 0:
            print(f"  LOW:  {name} ({uid}) - {total_videos} videos, {rate:.0%} YT ID rate ({with_yt}/{sampled})")
        else:
            print(f"  NONE: {name} ({uid}) - {total_videos} videos, 0% YT ID rate")

    # Results
    good_channels.sort(key=lambda x: x["total_videos"] * x["yt_rate"], reverse=True)

    print()
    print("=" * 70)
    print("CHANNELS WITH HIGH YOUTUBE ID RATES (>30%)")
    print("=" * 70)
    print(f"{'Name':<30} {'UID':<20} {'Videos':<8} {'YT Rate':<10} {'Est. w/ YT ID':<15}")
    print("-" * 83)

    total_estimated = 0
    for ch in good_channels:
        est = int(ch["total_videos"] * ch["yt_rate"])
        total_estimated += est
        print(f"  {ch['name'][:28]:<28} {ch['uid']:<20} {ch['total_videos']:<8} {ch['yt_rate']:<10.0%} ~{est}")

    print(f"\nTotal new channels: {len(good_channels)}")
    print(f"Estimated videos with YouTube IDs: ~{total_estimated}")
    print(f"\nUIDs to add:")
    for ch in good_channels:
        print(f"  \"{ch['uid']}\",  # {ch['name']} ({ch['total_videos']} videos, {ch['yt_rate']:.0%} YT rate)")


if __name__ == "__main__":
    asyncio.run(main())
