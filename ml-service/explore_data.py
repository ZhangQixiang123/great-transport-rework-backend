"""
Exploratory Data Analysis on collected competitor videos.
Understand the actual data distribution before redesigning the ML pipeline.
"""
import sys
import math
import sqlite3
import numpy as np

sys.path.insert(0, ".")

DB_PATH = "data.db"


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT bvid, bilibili_uid, title, description, duration,
               views, likes, coins, favorites, shares, danmaku, comments,
               publish_time, youtube_source_id, label
        FROM competitor_videos
        WHERE label IS NOT NULL
        ORDER BY views DESC
    """).fetchall()

    print(f"Total labeled videos: {len(rows)}")
    print()

    # Basic stats
    views = np.array([r["views"] for r in rows])
    likes = np.array([r["likes"] for r in rows])
    coins = np.array([r["coins"] for r in rows])
    favorites = np.array([r["favorites"] for r in rows])
    shares = np.array([r["shares"] for r in rows])
    danmaku = np.array([r["danmaku"] for r in rows])
    comments = np.array([r["comments"] for r in rows])
    duration = np.array([r["duration"] for r in rows])
    title_len = np.array([len(r["title"]) for r in rows])
    desc_len = np.array([len(r["description"] or "") for r in rows])
    has_yt = np.array([1 if r["youtube_source_id"] else 0 for r in rows])

    engagement = np.where(views > 0, (likes + coins + favorites) / views, 0)

    print("=" * 70)
    print("VIEWS DISTRIBUTION")
    print("=" * 70)
    percentiles = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    for p in percentiles:
        v = np.percentile(views, p)
        print(f"  P{p:>2}: {v:>12,.0f} views")
    print(f"  Mean:  {np.mean(views):>12,.0f}")
    print(f"  Stdev: {np.std(views):>12,.0f}")
    print(f"  Min:   {np.min(views):>12,.0f}")
    print(f"  Max:   {np.max(views):>12,.0f}")

    print()
    print("=" * 70)
    print("ENGAGEMENT RATE DISTRIBUTION (likes+coins+favorites)/views")
    print("=" * 70)
    for p in percentiles:
        e = np.percentile(engagement, p)
        print(f"  P{p:>2}: {e:>8.2%}")
    print(f"  Mean:  {np.mean(engagement):>8.2%}")

    print()
    print("=" * 70)
    print("LOG(VIEWS) DISTRIBUTION")
    print("=" * 70)
    log_views = np.log1p(views)
    for p in percentiles:
        lv = np.percentile(log_views, p)
        print(f"  P{p:>2}: {lv:>8.2f}  (= {math.expm1(lv):>10,.0f} views)")

    print()
    print("=" * 70)
    print("CONTENT FEATURES vs PERFORMANCE (correlation with log_views)")
    print("=" * 70)
    features = {
        "duration": duration,
        "title_length": title_len,
        "description_length": desc_len,
        "has_youtube_source": has_yt,
        "likes": likes,
        "coins": coins,
        "favorites": favorites,
        "shares": shares,
        "danmaku": danmaku,
        "comments": comments,
        "engagement_rate": engagement,
    }
    for name, feat in features.items():
        if np.std(feat) > 0:
            corr = np.corrcoef(log_views, feat)[0, 1]
            print(f"  {name:<25}: r = {corr:+.4f}")
        else:
            print(f"  {name:<25}: (constant)")

    print()
    print("=" * 70)
    print("PRE-UPLOAD FEATURES ONLY (what we can know before uploading)")
    print("=" * 70)
    pre_upload = {
        "duration": duration,
        "title_length": title_len,
        "description_length": desc_len,
        "has_youtube_source": has_yt,
    }
    for name, feat in pre_upload.items():
        if np.std(feat) > 0:
            corr = np.corrcoef(log_views, feat)[0, 1]
            print(f"  {name:<25}: r = {corr:+.4f}")
        else:
            print(f"  {name:<25}: (constant)")

    # Per-channel breakdown
    print()
    print("=" * 70)
    print("PER-CHANNEL STATISTICS")
    print("=" * 70)
    from collections import defaultdict
    channels = defaultdict(list)
    for r in rows:
        channels[r["bilibili_uid"]].append(r)

    print(f"{'UID':<20} {'Videos':<8} {'Med.Views':<12} {'Med.Engage':<12} {'P90 Views':<12}")
    print("-" * 64)
    for uid, vids in sorted(channels.items(), key=lambda x: -len(x[1])):
        ch_views = np.array([v["views"] for v in vids])
        ch_eng = np.where(ch_views > 0,
                          np.array([(v["likes"]+v["coins"]+v["favorites"]) for v in vids]) / ch_views,
                          0)
        name = vids[0]["title"][:15] if vids else uid
        print(f"  {uid:<18} {len(vids):<8} {np.median(ch_views):>10,.0f}  {np.median(ch_eng):>10.2%}  {np.percentile(ch_views, 90):>10,.0f}")

    # Proposed percentile-based labels
    print()
    print("=" * 70)
    print("PROPOSED DATA-DRIVEN LABEL THRESHOLDS (percentile-based)")
    print("=" * 70)
    p25 = np.percentile(views, 25)
    p50 = np.percentile(views, 50)
    p75 = np.percentile(views, 75)
    p90 = np.percentile(views, 90)
    p95 = np.percentile(views, 95)

    print(f"  Bottom 25%  (failed):     views < {p25:,.0f}")
    print(f"  25-75%      (standard):   {p25:,.0f} <= views < {p75:,.0f}")
    print(f"  75-95%      (successful): {p75:,.0f} <= views < {p95:,.0f}")
    print(f"  Top 5%      (viral):      views >= {p95:,.0f}")
    print()

    counts = {
        "failed": np.sum(views < p25),
        "standard": np.sum((views >= p25) & (views < p75)),
        "successful": np.sum((views >= p75) & (views < p95)),
        "viral": np.sum(views >= p95),
    }
    for label, count in counts.items():
        print(f"  {label:<12}: {count:>5} videos ({count/len(views)*100:.1f}%)")

    # Composite score idea
    print()
    print("=" * 70)
    print("COMPOSITE PERFORMANCE SCORE (for regression target)")
    print("=" * 70)
    # Score = weighted combo of log_views + engagement
    # Normalize both to 0-1 range first
    lv_norm = (log_views - log_views.min()) / (log_views.max() - log_views.min() + 1e-8)
    eng_norm = (engagement - engagement.min()) / (engagement.max() - engagement.min() + 1e-8)
    # 70% views, 30% engagement
    score = 0.7 * lv_norm + 0.3 * eng_norm

    for p in percentiles:
        s = np.percentile(score, p)
        print(f"  P{p:>2}: {s:.4f}")
    print(f"  Mean: {np.mean(score):.4f}")

    conn.close()


if __name__ == "__main__":
    main()
