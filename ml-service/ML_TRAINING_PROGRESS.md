# ML Training Pipeline Progress

## Overview

LightGBM regression model that predicts Bilibili view counts for YouTube-to-Bilibili transported videos. Uses pre-upload content features, clickbait signals, YouTube original stats, and title embeddings as inputs.

Two training modes:
- **Pure LightGBM** (`use_random_intercepts=False`): Better for predicting unseen/new channels.
- **GPBoost mixed effects** (`use_random_intercepts=True`): Better for known channels (per-channel random intercepts).

Current production model: **pure LightGBM** (optimized for new channel prediction).

## Model Performance

### Train Set (all 7,743 samples)

| Metric | Value |
|--------|-------|
| RMSE | 0.32 (log scale) |
| MAE | 0.23 (log scale) |
| Median AE | 0.17 (log scale) |
| R2 | 0.981 |
| Correlation | 0.991 |
| Within 2.7x | 98.8% |
| Within 7.4x | 99.97% |

### Cross-Channel CV (5-fold GroupKFold, unseen channels)

This is the metric that matters for predicting new channels.

| Metric | Value |
|--------|-------|
| Mean RMSE | 2.28 (log scale) |
| Mean MAE | 1.83 (log scale) |
| Mean R2 | -0.37 |
| Mean Correlation | 0.27 |
| Mean within 2.7x | 35.7% |
| Mean within 7.4x | 65.0% |

Per-fold breakdown:

| Fold | R2 | RMSE | Test Size |
|------|-----|------|-----------|
| 0 | -0.40 | 1.84 | 1,550 |
| 1 | +0.09 | 1.97 | 1,537 |
| 2 | +0.10 | 2.02 | 1,533 |
| 3 | -0.94 | 3.54 | 1,579 |
| 4 | -0.72 | 2.03 | 1,544 |

Note: Negative R2 means the model is worse than predicting the global mean for those channels. Fold 3 contains channels whose view distributions differ significantly from the rest. Two folds (1, 2) have positive R2.

## Features (43 total)

### Pre-upload features (10)
Available before uploading to Bilibili:
- `duration`, `duration_bucket` - video length
- `title_length`, `title_has_number` - title characteristics
- `description_length` - description length
- `publish_hour_sin`, `publish_hour_cos` - cyclical hour encoding
- `publish_dow_sin`, `publish_dow_cos` - cyclical day-of-week encoding
- `has_youtube_source` - whether YouTube source ID is detected

### Clickbait features (3)
- `title_exclamation_count` - count of `!` and `！` in title
- `title_question_count` - count of `?` and `？` in title
- `title_caps_ratio` - ratio of uppercase to alphabetic characters

### YouTube original stats (7)
From the source YouTube video (via YouTube Data API):
- `yt_log_views`, `yt_log_likes`, `yt_log_comments` - YouTube performance (log scale)
- `yt_duration_seconds` - YouTube video duration
- `yt_like_view_ratio`, `yt_comment_view_ratio` - YouTube engagement ratios
- `yt_category_id` - YouTube category

### Additional features (3)
- `yt_tag_count` - number of YouTube tags
- `yt_upload_delay_days` - days between YouTube and Bilibili publish
- `yt_stats_imputed` - flag for imputed vs real YouTube stats

### Title embeddings (20)
- `title_emb_0` .. `title_emb_19` - PCA-reduced sentence-transformer embeddings

### Top 10 features by importance (gain)

| Rank | Feature | Gain |
|------|---------|------|
| 1 | yt_log_comments | 49,301 |
| 2 | title_length | 44,108 |
| 3 | description_length | 41,287 |
| 4 | yt_upload_delay_days | 35,604 |
| 5 | title_caps_ratio | 24,485 |
| 6 | yt_category_id | 19,809 |
| 7 | title_emb_3 | 15,947 |
| 8 | yt_log_likes | 14,427 |
| 9 | yt_like_view_ratio | 13,308 |
| 10 | duration | 12,904 |

## Classification Thresholds (data-driven)

Derived from regression prediction percentiles on all training data:

| Label | Condition | Predicted Views |
|-------|-----------|----------------|
| failed | bottom 25% | < 455 |
| standard | 25-75% | 455 - 5,495 |
| successful | 75-95% | 5,495 - 52,041 |
| viral | top 5% | > 52,041 |

## Database Schema

### `competitor_channels` (31 rows)

| Column | Type | Description |
|--------|------|-------------|
| bilibili_uid | TEXT (PK) | Bilibili user ID |
| name | TEXT | Channel name |
| description | TEXT | Channel description |
| follower_count | INTEGER | Current follower count |
| video_count | INTEGER | Total videos on channel |
| added_at | TIMESTAMP | When channel was added |
| is_active | INTEGER | Whether channel is actively tracked |

### `competitor_videos` (7,791 rows)

| Column | Type | Description |
|--------|------|-------------|
| bvid | TEXT (PK) | Bilibili video ID |
| bilibili_uid | TEXT (FK) | Channel that uploaded |
| title | TEXT | Video title |
| description | TEXT | Video description |
| duration | INTEGER | Duration in seconds |
| views | INTEGER | View count at collection time |
| likes | INTEGER | Like count |
| coins | INTEGER | Coin count |
| favorites | INTEGER | Favorite count |
| shares | INTEGER | Share count |
| danmaku | INTEGER | Danmaku (bullet comment) count |
| comments | INTEGER | Comment count |
| publish_time | TIMESTAMP | When video was published |
| collected_at | TIMESTAMP | When stats were collected |
| youtube_source_id | TEXT | YouTube video ID (if detected) |
| label | TEXT | Manual label (rarely used) |

### `youtube_stats` (4,600 rows)

| Column | Type | Description |
|--------|------|-------------|
| youtube_id | TEXT | YouTube video ID |
| bvid | TEXT (FK) | Linked Bilibili video |
| yt_title | TEXT | YouTube video title |
| yt_channel_title | TEXT | YouTube channel name |
| yt_views | INTEGER | YouTube view count |
| yt_likes | INTEGER | YouTube like count |
| yt_comments | INTEGER | YouTube comment count |
| yt_duration_seconds | INTEGER | YouTube video duration |
| yt_published_at | TEXT | YouTube publish timestamp |
| yt_category_id | INTEGER | YouTube category ID |
| yt_tags | TEXT | YouTube tags (JSON array) |
| match_method | TEXT | How match was found (`source_id` or `title_search`) |
| fetched_at | TIMESTAMP | When stats were fetched |

Only `source_id` matches are used for training (title search matches are too noisy).

## Data Collection

### Round 1 (10 channels)
- 2,064 videos from manually identified independent transporter channels
- 247 with YouTube source IDs

### Round 2 (21 channels)
- Discovered via `discover_yt_channels.py` — channels with >30% YouTube ID reference rates
- 5,727 videos collected (300 per channel max)
- 5,107 with YouTube source IDs

### Total Dataset
- **7,791 videos** from 31 channels
- **5,439** with YouTube source IDs in video metadata
- **4,493** enriched with YouTube original stats (via YouTube Data API, source_id match only)

## Key Design Decisions

1. **Regression over classification**: Predicting continuous log(views) is more informative than arbitrary label categories. Classification is derived from regression output using percentile thresholds.

2. **No circular features**: Previous approach used Bilibili views/likes/coins as inputs to predict labels derived from those same metrics. Current approach uses only pre-upload features + YouTube stats.

3. **Only source_id YouTube matches**: Title-search matching produced wrong YouTube video matches (e.g., a BYD car video matching a random YouTube result). Only videos with explicit YouTube source IDs in their Bilibili metadata are used.

4. **Percentile-based thresholds**: Instead of hardcoded view thresholds, thresholds are derived from the actual prediction distribution.

5. **Pure LightGBM for new channels**: GPBoost mixed effects model achieves R2=0.98 on known channels but R2=-0.61 on unseen channels (random intercept absorbs all channel variance during training but is 0 for new channels). Pure LightGBM without random intercepts gives CV R2=-0.37 on unseen channels — still negative but meaningfully better (correlation 0.27 vs 0.04).

6. **No channel_log_followers feature**: Although follower count correlates with average views (r=0.56), it's unavailable at cold start when first evaluating a new channel.

## Model Parameters

```json
{
  "objective": "regression_l2",
  "learning_rate": 0.05,
  "num_leaves": 63,
  "feature_fraction": 0.8,
  "min_data_in_leaf": 20,
  "num_boost_round": 500
}
```

## Files

### Training pipeline
- `app/training/features.py` - Feature extraction (43 features)
- `app/training/trainer.py` - LightGBM/GPBoost regression training
- `app/training/evaluator.py` - Regression metrics (RMSE, R2, MAE, correlation)
- `app/training/data_validator.py` - Data validation
- `app/models/ranker.py` - Inference wrapper (auto-detects model type from metadata)

### Data collection scripts
- `discover_channels.py` - Round 1 channel discovery
- `discover_yt_channels.py` - Round 2 channel discovery (high YT ID rates)
- `collect_all.py` - Round 1 batch collection
- `collect_round2.py` - Round 2 batch collection
- `enrich_youtube.py` - YouTube stats enrichment via API
- `generate_embeddings.py` - Title embedding generation
- `populate_followers.py` - Follower count collection

### Tests
- `tests/test_features.py` - 46 tests for feature extraction
- `tests/test_evaluator.py` - 9 tests for regression evaluation
- `tests/test_trainer.py` - 6 tests for training pipeline
- `tests/test_ranker.py` - 12 tests for inference wrapper
- 74 tests passing (+ 2 pre-existing failures in unrelated test files)

## Limitations

- **Cross-channel generalization is weak**: CV R2=-0.37 for unseen channels means predictions for new channels are unreliable. The model can rank videos within a channel moderately well (correlation 0.27) but absolute view count predictions will be off.
- **31 channels is a small training set**: More diverse channels would improve generalization.
- **No early view data**: Literature shows R2 jumps to ~0.80+ when early view counts (first hours/days) are available. Without them, prediction ceiling is fundamentally limited.
- **Bilibili recommendation algorithm**: Introduces irreducible stochasticity — identical videos can get very different view counts depending on recommendation placement.
