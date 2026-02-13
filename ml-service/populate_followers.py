"""Populate follower_count and video_count for all competitor channels from Bilibili API."""
import asyncio
import sqlite3
import time

from bilibili_api import user


async def main():
    conn = sqlite3.connect("data.db")
    conn.row_factory = sqlite3.Row
    channels = conn.execute(
        "SELECT bilibili_uid, name FROM competitor_channels"
    ).fetchall()

    print(f"Fetching info for {len(channels)} channels...")

    updated = 0
    for ch in channels:
        uid = ch["bilibili_uid"]
        name = ch["name"]
        try:
            u = user.User(uid=int(uid))

            # get_relation_info() returns follower count
            rel = await u.get_relation_info()
            followers = rel.get("follower", 0)

            # get_videos page count gives total videos
            vids_result = await u.get_videos(pn=1, ps=1)
            total_videos = vids_result.get("page", {}).get("count", 0) if vids_result else 0

            conn.execute(
                "UPDATE competitor_channels SET follower_count = ?, video_count = ? WHERE bilibili_uid = ?",
                (followers, total_videos, uid),
            )
            conn.commit()
            updated += 1
            print(f"  [{updated}/{len(channels)}] uid={uid} followers={followers:,} videos={total_videos}")
        except Exception as e:
            print(f"  ERROR uid={uid} name={name}: {e}")

        time.sleep(1.0)  # rate limit

    conn.close()
    print(f"\nDone. Updated {updated}/{len(channels)} channels.")


if __name__ == "__main__":
    asyncio.run(main())
