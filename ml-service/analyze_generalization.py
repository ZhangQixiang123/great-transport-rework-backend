import json, math, sqlite3, sys, statistics
from collections import defaultdict

import os
BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "data.db")
META = os.path.join(BASE, "models", "latest_model_meta.json")

def pearson(x, y):
    n = len(x)
    if n < 3: return 0.0, 1.0
    mx = sum(x)/n
    my = sum(y)/n
    sx = math.sqrt(sum((xi-mx)**2 for xi in x)/(n-1))
    sy = math.sqrt(sum((yi-my)**2 for yi in y)/(n-1))
    if sx == 0 or sy == 0: return 0.0, 1.0
    r = sum((xi-mx)*(yi-my) for xi,yi in zip(x,y))/((n-1)*sx*sy)
    r = max(-1.0, min(1.0, r))
    if abs(r) >= 0.9999: return r, 0.0
    t = r * math.sqrt((n-2)/(1-r*r))
    z = abs(t)
    p = 2.0 * 0.5 * math.erfc(z / math.sqrt(2))
    return r, p

# PART 1: Feature Importances
print("=" * 70)
print("PART 1: FEATURE IMPORTANCES (sorted, LightGBM split gain)")
print("=" * 70)

with open(META, "r", encoding="utf-8") as f:
    meta = json.load(f)

fi = meta["evaluation"]["feature_importance"]
sorted_fi = sorted(fi.items(), key=lambda x: x[1], reverse=True)
total_gain = sum(v for _, v in sorted_fi)

print("{:<5} {:<30} {:>10} {:>10}".format("Rank", "Feature", "Gain", "% Total"))
print("-" * 58)
for i, (feat, gain) in enumerate(sorted_fi, 1):
    pct = 100.0 * gain / total_gain
    print("{:<5} {:<30} {:>10.1f} {:>9.1f}%".format(i, feat, gain, pct))

# PART 2: Per-Channel Variance
print()
print("=" * 70)
print("PART 2: PER-CHANNEL VARIANCE ANALYSIS")
print("=" * 70)

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

rows = conn.execute("""
    SELECT bvid, bilibili_uid, title, views, youtube_source_id,
           duration, description, publish_time, likes, coins,
           favorites, shares, danmaku, comments
    FROM competitor_videos
    WHERE views > 0
""").fetchall()

channel_data = defaultdict(list)
all_log_views = []
video_records = []
for r in rows:
    lv = math.log1p(max(int(r["views"]), 0))
    uid = r["bilibili_uid"]
    channel_data[uid].append(lv)
    all_log_views.append(lv)
    rec = dict(r)
    rec["log_views"] = lv
    video_records.append(rec)

ch_stats = []
for uid, vals in channel_data.items():
    n = len(vals)
    mn = sum(vals) / n
    sd = math.sqrt(sum((v - mn)**2 for v in vals) / (n - 1)) if n > 1 else 0.0
    mi = min(vals)
    ma = max(vals)
    ch_stats.append((uid, n, mn, sd, mi, ma, ma - mi))

ch_stats.sort(key=lambda x: x[1], reverse=True)

print("{:<20} {:>6} {:>8} {:>8} {:>8} {:>8} {:>8}".format(
    "Channel", "Count", "Mean", "StdDev", "Min", "Max", "Range"))
print("-" * 76)
for uid, n, mn, sd, mi, ma, rng in ch_stats:
    print("{:<20} {:>6} {:>8.2f} {:>8.2f} {:>8.2f} {:>8.2f} {:>8.2f}".format(
        uid, n, mn, sd, mi, ma, rng))

overall_mean = sum(all_log_views) / len(all_log_views)
overall_std = math.sqrt(sum((v - overall_mean)**2 for v in all_log_views) / (len(all_log_views) - 1))
avg_within_std = sum(s[3] for s in ch_stats) / len(ch_stats)

print()
print("Overall: N={}, mean_log_views={:.3f}, overall_std={:.3f}".format(
    len(all_log_views), overall_mean, overall_std))
print("Average within-channel std: {:.3f}".format(avg_within_std))

# PART 3: ANOVA Variance Decomposition
print()
print("=" * 70)
print("PART 3: ANOVA-STYLE VARIANCE DECOMPOSITION")
print("=" * 70)

grand_mean = overall_mean
total_ss = sum((v - grand_mean)**2 for v in all_log_views)

between_ss = 0.0
within_ss = 0.0
for uid, vals in channel_data.items():
    ch_mean = sum(vals) / len(vals)
    n_i = len(vals)
    between_ss += n_i * (ch_mean - grand_mean)**2
    within_ss += sum((v - ch_mean)**2 for v in vals)

print("Total SS:           {:>12.2f}".format(total_ss))
print("Between-channel SS: {:>12.2f}  ({:.1f}%)".format(between_ss, 100*between_ss/total_ss))
print("Within-channel SS:  {:>12.2f}  ({:.1f}%)".format(within_ss, 100*within_ss/total_ss))
print()
print("Eta-squared (between/total): {:.4f}".format(between_ss/total_ss))
print()
print("INTERPRETATION: {:.1f}% of all variance in log(views)".format(100*between_ss/total_ss))
print("is explained purely by which channel uploaded the video.")
print("The model only needs to predict the remaining {:.1f}%.".format(100*within_ss/total_ss))
print()
within_rmse = math.sqrt(within_ss / len(all_log_views))
print("If channel_mean perfectly captures between-channel variance,")
print("residual RMSE should be sqrt(within_SS/N) = {:.3f}".format(within_rmse))

# PART 4: Feature Correlations with Relative Target
print()
print("=" * 70)
print("PART 4: FEATURE CORRELATIONS WITH WITHIN-CHANNEL RELATIVE PERFORMANCE")
print("=" * 70)

yt_rows = conn.execute("""
    SELECT bvid, yt_views, yt_likes, yt_comments,
           yt_duration_seconds, yt_category_id, yt_tags, yt_published_at
    FROM youtube_stats
    WHERE match_method = "source_id"
""").fetchall()
yt_map = {r["bvid"]: dict(r) for r in yt_rows}

ch_means = {}
for uid, vals in channel_data.items():
    ch_means[uid] = sum(vals) / len(vals)

merged = []
for rec in video_records:
    bvid = rec["bvid"]
    if bvid not in yt_map:
        continue
    yt = yt_map[bvid]
    uid = rec["bilibili_uid"]
    ch_mean = ch_means.get(uid, grand_mean)
    relative_lv = rec["log_views"] - ch_mean

    yt_views = max(int(yt["yt_views"] or 0), 0)
    yt_likes = max(int(yt["yt_likes"] or 0), 0)
    yt_comments_val = max(int(yt["yt_comments"] or 0), 0)
    yt_dur = max(int(yt["yt_duration_seconds"] or 0), 0)
    yt_cat = int(yt["yt_category_id"] or 0)

    yt_log_views = math.log1p(yt_views)
    yt_log_likes = math.log1p(yt_likes)
    yt_log_comments = math.log1p(yt_comments_val)
    yt_lvr = yt_likes / yt_views if yt_views > 0 else 0.0
    yt_cvr = yt_comments_val / yt_views if yt_views > 0 else 0.0

    tag_count = 0
    if yt["yt_tags"]:
        try:
            tags = json.loads(yt["yt_tags"])
            if isinstance(tags, list):
                tag_count = len(tags)
        except Exception:
            pass

    title_len = len(rec["title"] or "")
    desc_len = len(rec["description"] or "")
    duration = max(int(rec["duration"] or 0), 0)

    merged.append({
        "bvid": bvid, "uid": uid,
        "log_views": rec["log_views"],
        "relative_log_views": relative_lv,
        "yt_log_views": yt_log_views,
        "yt_log_likes": yt_log_likes,
        "yt_log_comments": yt_log_comments,
        "yt_like_view_ratio": yt_lvr,
        "yt_comment_view_ratio": yt_cvr,
        "yt_duration_seconds": float(yt_dur),
        "yt_category_id": float(yt_cat),
        "yt_tag_count": float(tag_count),
        "duration": float(duration),
        "title_length": float(title_len),
        "description_length": float(desc_len),
    })

print("Videos with real YouTube stats: {} out of {} total".format(len(merged), len(video_records)))
print()
# Compute relative YT performance
yt_ch_data = defaultdict(list)
for rec in merged:
    yt_ch_data[rec["uid"]].append(rec["yt_log_views"])
yt_ch_means = {}
for uid, vals in yt_ch_data.items():
    yt_ch_means[uid] = sum(vals) / len(vals)
for rec in merged:
    rec["relative_yt_log_views"] = rec["yt_log_views"] - yt_ch_means.get(rec["uid"], 0)

target_vals = [r["relative_log_views"] for r in merged]

features_to_test = [
    "yt_log_views", "yt_log_likes", "yt_log_comments",
    "yt_like_view_ratio", "yt_comment_view_ratio",
    "yt_duration_seconds", "yt_category_id", "yt_tag_count",
    "duration", "title_length", "description_length",
    "relative_yt_log_views",
]

results = []
for feat in features_to_test:
    vals = [r[feat] for r in merged]
    r_val, p_val = pearson(vals, target_vals)
    results.append((feat, r_val, p_val))

results.sort(key=lambda x: abs(x[1]), reverse=True)

print("{:<30} {:>16} {:>12}".format("Feature", "Corr w/ relative", "p-value"))
print("-" * 60)
for feat, r_val, p_val in results:
    sig = "***" if p_val < 0.001 else "**" if p_val < 0.01 else "*" if p_val < 0.05 else ""
    print("{:<30} {:>+10.4f}       {:>10.2e}  {}".format(feat, r_val, p_val, sig))

# PART 5: YT Engagement Ratios vs Bilibili Relative Performance
print()
print("=" * 70)
print("PART 5: YT ENGAGEMENT RATIOS vs BILIBILI RELATIVE PERFORMANCE")
print("=" * 70)

for ratio_col in ["yt_like_view_ratio", "yt_comment_view_ratio"]:
    vals = [r[ratio_col] for r in merged]
    tvals = [r["relative_log_views"] for r in merged]
    pairs = [(v, t) for v, t in zip(vals, tvals) if math.isfinite(v) and math.isfinite(t)]
    if len(pairs) < 30:
        print("{}: insufficient data (n={})".format(ratio_col, len(pairs)))
        continue
    x_vals = [p[0] for p in pairs]
    y_vals = [p[1] for p in pairs]
    r_val, p_val = pearson(x_vals, y_vals)
    print()
    print("{} vs bilibili relative log(views):".format(ratio_col))
    print("  Pearson r = {:+.4f}, p = {:.2e}, n = {}".format(r_val, p_val, len(pairs)))

    sorted_pairs = sorted(pairs, key=lambda p: p[0])
    q_size = len(sorted_pairs) // 4
    qlabels = ["Q1(low)", "Q2", "Q3", "Q4(high)"]
    print("  Quartile breakdown (relative B-site log_views):")
    for qi in range(4):
        start = qi * q_size
        end = start + q_size if qi < 3 else len(sorted_pairs)
        q_y = [p[1] for p in sorted_pairs[start:end]]
        q_mean = sum(q_y) / len(q_y)
        q_std = math.sqrt(sum((v - q_mean)**2 for v in q_y) / (len(q_y) - 1)) if len(q_y) > 1 else 0.0
        print("    {}: mean={:+.3f}, std={:.3f}, n={}".format(qlabels[qi], q_mean, q_std, len(q_y)))

# Key test: relative YT -> relative Bilibili
print()
print("-" * 70)
print("KEY TEST: Does relative YT performance predict relative Bilibili performance?")
print("-" * 70)

x_rel = [r["relative_yt_log_views"] for r in merged]
y_rel = [r["relative_log_views"] for r in merged]
r_key, p_key = pearson(x_rel, y_rel)
print("  Pearson r = {:+.4f}, p = {:.2e}, n = {}".format(r_key, p_key, len(merged)))
print()

sorted_rel = sorted(zip(x_rel, y_rel), key=lambda p: p[0])
q5_size = len(sorted_rel) // 5
q5_labels = ["Q1(low)", "Q2", "Q3", "Q4", "Q5(high)"]
print("  Quintile breakdown:")
for qi in range(5):
    start = qi * q5_size
    end = start + q5_size if qi < 4 else len(sorted_rel)
    q_y = [p[1] for p in sorted_rel[start:end]]
    q_mean = sum(q_y) / len(q_y)
    q_std = math.sqrt(sum((v - q_mean)**2 for v in q_y) / (len(q_y) - 1)) if len(q_y) > 1 else 0.0
    print("    {}: mean_relative_bili={:+.3f}, std={:.3f}, n={}".format(
        q5_labels[qi], q_mean, q_std, len(q_y)))

# Simple OLS
n = len(merged)
mx = sum(x_rel) / n
my = sum(y_rel) / n
sxx = sum((xi - mx)**2 for xi in x_rel)
sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(x_rel, y_rel))
slope = sxy / sxx if sxx > 0 else 0.0
intercept = my - slope * mx
y_pred = [slope * xi + intercept for xi in x_rel]
ss_res = sum((yi - yp)**2 for yi, yp in zip(y_rel, y_pred))
ss_tot = sum((yi - my)**2 for yi in y_rel)
r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
resid_rmse = math.sqrt(ss_res / n)
total_std_y = math.sqrt(ss_tot / (n - 1))

print()
print("  Simple OLS: relative_bili = {:+.4f} * relative_yt + {:+.4f}".format(slope, intercept))
print("  R2 = {:.4f}".format(r2))
print("  Residual RMSE = {:.4f}".format(resid_rmse))
print("  Total std of relative bili log_views = {:.4f}".format(total_std_y))

# SUMMARY
print()
print("=" * 70)
print("SUMMARY")
print("=" * 70)

cv_r2 = meta["cv_evaluation"]["mean_r2"]
holdout_r2 = meta["evaluation"]["r2"]
strength = "WEAK" if abs(r_key) < 0.3 else "MODERATE" if abs(r_key) < 0.5 else "STRONG"
direction = "do" if r_key > 0 else "do NOT"

print()
print("Key findings:")
print("1. Between-channel variance is {:.1f}% of total.".format(100 * between_ss / total_ss))
print("   -> The model can get far just by learning channel_mean_log_views.")
print("   -> But cross-channel CV splits entire channels into test, so this")
print("      feature is unknown for new channels. That is why CV R2 is negative.")
print()
print("2. Within-channel std (avg): {:.3f} log-units.".format(avg_within_std))
print("   -> This is the predictable variance for known channels.")
print()
print("3. Relative YT perf -> relative Bilibili perf: r={:+.4f}".format(r_key))
print("   -> {} signal.".format(strength))
print("   -> Videos that outperform on YouTube {} tend to".format(direction))
print("      outperform on Bilibili too (within the same channel).")
print()
print("4. Model cross-channel CV: R2={:.3f} (terrible - cannot predict".format(cv_r2))
print("   channel-level mean for unseen channels).")
print("   Holdout eval: R2={:.3f} (includes known channels, so".format(holdout_r2))
print("   channel_mean_log_views helps).")
print()
print("5. Top feature importances show description_length, duration, and")
print("   title_length dominate - but these may be channel-specific proxies")
print("   rather than truly generalizable signals.")

conn.close()
