# ML Training Pipeline Progress

## Overview

LightGBM regression model that predicts Bilibili view counts for YouTube-to-Bilibili transported videos. Uses pre-upload content features and YouTube original stats as inputs.

## Model Performance

| Metric | Value |
|--------|-------|
| RMSE | 1.34 (log scale) |
| MAE | 0.99 (log scale) |
| Median AE | 0.72 (log scale) |
| R2 | 0.661 |
| Correlation | 0.813 |
| Within 2.7x | 62.0% |
| Within 7.4x | 87.7% |

- Train: 6,194 samples / Test: 1,549 samples
- Best iteration: 140 (early stopped from 1,000 max)

## Features (15 total)

### Pre-upload features (8)
Available before uploading to Bilibili:
- `duration`, `duration_bucket` - video length
- `title_length`, `title_has_number` - title characteristics
- `description_length` - description length
- `publish_hour`, `publish_day_of_week` - upload timing
- `has_youtube_source` - whether YouTube source ID is detected

### YouTube original stats (7)
From the source YouTube video (via YouTube Data API):
- `yt_log_views`, `yt_log_likes`, `yt_log_comments` - YouTube performance (log scale)
- `yt_duration_seconds` - YouTube video duration
- `yt_like_view_ratio`, `yt_comment_view_ratio` - YouTube engagement ratios
- `yt_category_id` - YouTube category

### Top features by importance (gain)
1. description_length (52,126)
2. title_length (36,292)
3. duration (23,806)
4. yt_category_id (23,672)
5. yt_log_comments (16,825)
6. yt_log_views (12,461)
7. yt_log_likes (11,390)
8. yt_duration_seconds (11,331)

## Classification Thresholds (data-driven)

Derived from regression prediction percentiles:

| Label | Percentile | Predicted Views |
|-------|-----------|----------------|
| failed | bottom 25% | < 612 |
| standard | 25-75% | 612 - 4,177 |
| successful | 75-95% | 4,177 - 24,933 |
| viral | top 5% | > 24,933 |

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
- **4,541** enriched with YouTube original stats (via YouTube Data API, source_id match only)

### YouTube Stats Enrichment
- Phase 1: Batch fetch by known YouTube IDs (reliable, `match_method='source_id'`)
- Phase 2: Title search matching (unreliable, discarded for training — too noisy)
- Only `source_id` matches are used in the model

## Key Design Decisions

1. **Regression over classification**: Predicting continuous log(views) is more informative than arbitrary label categories. Classification is derived from regression output.

2. **No circular features**: Previous approach used Bilibili views/likes/coins as inputs to predict labels derived from those same metrics. New approach uses only pre-upload features + YouTube stats.

3. **Only source_id YouTube matches**: Title-search matching produced wrong YouTube video matches (e.g., a BYD car video matching a random YouTube result). Only videos with explicit YouTube source IDs in their Bilibili metadata are used.

4. **Percentile-based thresholds**: Instead of hardcoded view thresholds (1M for viral), thresholds are derived from the actual data distribution.

## Files

### Training pipeline
- `app/training/features.py` - Feature extraction (15 features)
- `app/training/trainer.py` - LightGBM regression training
- `app/training/evaluator.py` - Regression metrics (RMSE, R2, MAE, correlation)
- `app/training/data_validator.py` - Data validation

### Data collection scripts
- `discover_channels.py` - Round 1 channel discovery
- `discover_yt_channels.py` - Round 2 channel discovery (high YT ID rates)
- `collect_all.py` - Round 1 batch collection
- `collect_round2.py` - Round 2 batch collection
- `enrich_youtube.py` - YouTube stats enrichment via API
- `explore_data.py` - Exploratory data analysis

### Tests
- `tests/test_features.py` - 29 tests for feature extraction
- `tests/test_evaluator.py` - 7 tests for regression evaluation
- `tests/test_trainer.py` - 5 tests for training pipeline
- All 41 tests passing

## Next Steps

- Add title/thumbnail embeddings (DistilBERT/CLIP) for richer content features
- Integrate model into FastAPI prediction endpoint
- Collect more data from additional transporter channels
- Cross-validation for more robust evaluation
- Channel-level features (channel size, historical performance)
