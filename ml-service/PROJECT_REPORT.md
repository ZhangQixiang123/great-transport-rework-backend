# Great Transport Rework — ML Service Technical Report

## 1. Project Overview

This project predicts which English YouTube videos will perform well when translated and re-uploaded ("transported") to Bilibili, the leading Chinese video platform. It combines:

- **ML pipeline**: LightGBM regression model trained on 7,791 competitor videos to predict Bilibili view counts from pre-upload signals
- **Discovery pipeline**: Automated system that fetches Bilibili trending keywords, translates them to English via a local LLM, searches YouTube, scores relevance, and ranks candidates by predicted performance

**Tech stack**: Python 3.14, SQLite, LightGBM/GPBoost, Ollama + Qwen 2.5:7b, YouTube Data API, Bilibili API

---

## 2. Database Schema

All data is stored in a single SQLite database (`data.db`). There are three table groups.

### 2.1 Upload Tracking (Phase 2)

Tracks our own uploads to Bilibili and their performance over time.

```sql
-- Performance snapshots at checkpoint intervals (1h, 6h, 24h, 48h, 168h, 720h)
CREATE TABLE upload_performance (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id         TEXT NOT NULL,
    checkpoint_hours  INTEGER NOT NULL,
    recorded_at       TIMESTAMP NOT NULL,
    views             INTEGER DEFAULT 0,
    likes             INTEGER DEFAULT 0,
    coins             INTEGER DEFAULT 0,
    favorites         INTEGER DEFAULT 0,
    shares            INTEGER DEFAULT 0,
    danmaku           INTEGER DEFAULT 0,
    comments          INTEGER DEFAULT 0,
    view_velocity     REAL DEFAULT 0.0,
    engagement_rate   REAL DEFAULT 0.0,
    UNIQUE(upload_id, checkpoint_hours)
);

-- Final outcome label for each upload
CREATE TABLE upload_outcomes (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id             TEXT NOT NULL UNIQUE,
    label                 TEXT NOT NULL,  -- 'viral' | 'successful' | 'standard' | 'failed'
    labeled_at            TIMESTAMP NOT NULL,
    final_views           INTEGER DEFAULT 0,
    final_engagement_rate REAL DEFAULT 0.0,
    final_coins           INTEGER DEFAULT 0
);
```

### 2.2 Competitor Monitoring (Phase 3B)

The training data source. Stores videos from 31 competitor channels and their YouTube origins.

```sql
CREATE TABLE competitor_channels (
    bilibili_uid   TEXT PRIMARY KEY,
    name           TEXT,
    description    TEXT,
    follower_count INTEGER DEFAULT 0,
    video_count    INTEGER DEFAULT 0,
    added_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_active      INTEGER DEFAULT 1
);

CREATE TABLE competitor_videos (
    bvid              TEXT PRIMARY KEY,
    bilibili_uid      TEXT NOT NULL REFERENCES competitor_channels(bilibili_uid),
    title             TEXT,
    description       TEXT,
    duration          INTEGER DEFAULT 0,
    views             INTEGER DEFAULT 0,
    likes             INTEGER DEFAULT 0,
    coins             INTEGER DEFAULT 0,
    favorites         INTEGER DEFAULT 0,
    shares            INTEGER DEFAULT 0,
    danmaku           INTEGER DEFAULT 0,
    comments          INTEGER DEFAULT 0,
    publish_time      TIMESTAMP,
    collected_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    youtube_source_id TEXT,        -- YouTube video ID this was transported from
    label             TEXT         -- 'viral' | 'successful' | 'standard' | 'failed'
);
-- Indexes: idx_competitor_videos_uid, idx_competitor_videos_label
```

A separate `youtube_stats` table (managed by `enrich_youtube.py`) stores the original YouTube video stats joined via `youtube_source_id`.

### 2.3 Discovery Pipeline

Stores each discovery run and its ranked recommendations.

```sql
CREATE TABLE discovery_runs (
    run_id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    keywords_fetched      INTEGER,
    candidates_found      INTEGER,
    recommendations_count INTEGER
);

CREATE TABLE discovery_recommendations (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                    INTEGER REFERENCES discovery_runs(run_id),
    keyword                   TEXT NOT NULL,
    heat_score                INTEGER,
    youtube_video_id          TEXT NOT NULL,
    youtube_title             TEXT,
    youtube_channel           TEXT,
    youtube_views             INTEGER,
    youtube_likes             INTEGER,
    youtube_duration_seconds  INTEGER,
    relevance_score           REAL,       -- LLM score 0.0-1.0
    relevance_reasoning       TEXT,       -- LLM explanation
    predicted_log_views       REAL,       -- ML model output (log scale)
    predicted_views           REAL,       -- exp(predicted_log_views) - 1
    predicted_label           TEXT,       -- derived classification
    combined_score            REAL,       -- final ranking score
    created_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- Indexes: idx_discovery_rec_run, idx_discovery_rec_score
```

### 2.4 Entity Relationship

```
competitor_channels  1──M  competitor_videos  M──1  youtube_stats
                                                         │
discovery_runs       1──M  discovery_recommendations     │
                               │ (youtube_video_id) ─────┘
                               │   checked against
                               └── competitor_videos.youtube_source_id (dedup)
```

---

## 3. ML Pipeline

### 3.1 Data Collection Workflow

```
add-competitor <uid>       Register a Bilibili channel to monitor
        │
collect-all-competitors    Scrape videos from all active channels
        │                  (extracts youtube_source_id from descriptions)
        │
enrich_youtube.py          Fetch YouTube stats for matched source videos
        │
label-videos               Auto-label by view count percentiles
        │                  (p25=failed, p25-p75=standard, p75-p95=successful, p95+=viral)
        │
train                      Train LightGBM regression model
```

**Current dataset**: 7,791 videos from 31 channels. 5,439 with YouTube source IDs, 4,493 with enriched YouTube stats.

### 3.2 Feature Engineering (43 features)

Extracted by `app/training/features.py`. All features are available at upload time (no circular data leakage).

| Group | Count | Features |
|-------|-------|----------|
| **Pre-upload content** | 5 | `duration`, `duration_bucket`, `title_length`, `title_has_number`, `description_length` |
| **Cyclical time** | 4 | `publish_hour_sin`, `publish_hour_cos`, `publish_dow_sin`, `publish_dow_cos` |
| **Source flag** | 1 | `has_youtube_source` |
| **Clickbait signals** | 3 | `title_exclamation_count`, `title_question_count`, `title_caps_ratio` |
| **YouTube original stats** | 7 | `yt_log_views`, `yt_log_likes`, `yt_log_comments`, `yt_duration_seconds`, `yt_like_view_ratio`, `yt_comment_view_ratio`, `yt_category_id` |
| **Additional** | 3 | `yt_tag_count`, `yt_upload_delay_days`, `yt_stats_imputed` |
| **Title embeddings** | 20 | `title_emb_0` .. `title_emb_19` (PCA-reduced sentence-transformer) |
| **Total** | **43** | |

Key design decisions:
- **No post-upload Bilibili metrics** (views, likes, coins) used as inputs — prevents data leakage
- **Cyclical encoding** for hour-of-day and day-of-week (sin/cos) instead of raw integers
- **YouTube stats imputation**: when missing, uses per-channel means or global means; `yt_stats_imputed` flag tells the model which rows are imputed
- **Title embeddings**: pre-computed with sentence-transformers, PCA-reduced to 20 dimensions

### 3.3 Training Process

Implemented in `app/training/trainer.py`. Function: `train_model()`.

**LightGBM hyperparameters**:
```python
{
    "objective": "regression_l2",
    "learning_rate": 0.05,
    "num_leaves": 63,
    "feature_fraction": 0.8,
    "min_data_in_leaf": 20,
}
```

**Training flow**:
1. Load labeled videos + regression targets (log1p of views)
2. Validate data (minimum 50 samples, at least 2 classes)
3. Load pre-computed title embeddings from `models/title_embeddings.npz`
4. Compute YouTube stats imputation means (per-channel and global)
5. **5-fold GroupKFold cross-validation** by channel (ensures unseen channels in each test fold)
   - Each fold: extract features, train model, evaluate on held-out channels
6. Train final model on all data (up to 500 rounds with early stopping)
7. Save artifacts:
   - `models/latest_model.json` — model weights
   - `models/latest_model_meta.json` — feature names, imputation stats, percentile thresholds, CV results

**Two training modes**:
- `use_random_intercepts=True`: GPBoost mixed-effects model with per-channel random intercepts. Excellent for known channels (train R2=0.98) but random intercepts absorb all channel variance, making predictions for unseen channels poor.
- `use_random_intercepts=False`: Pure LightGBM. Better cross-channel generalization. **This is the production mode.**

### 3.4 Evaluation Metrics

Computed by `app/training/evaluator.py`. The `RegressionReport` dataclass tracks:

| Metric | Description | Current Value |
|--------|-------------|---------------|
| RMSE | Root mean squared error (log scale) | 0.32 (train) / 2.28 (CV) |
| MAE | Mean absolute error (log scale) | 0.23 (train) / 1.83 (CV) |
| Median AE | Median absolute error | 0.17 (train) |
| R2 | Coefficient of determination | 0.981 (train) / -0.37 (CV) |
| Correlation | Pearson correlation | 0.991 (train) / 0.27 (CV) |
| Within 2.7x | % predictions within 1 log unit | 98.8% (train) / 35.7% (CV) |
| Within 7.4x | % predictions within 2 log units | 99.97% (train) / 65.0% (CV) |

The large gap between train and CV metrics shows that channel identity dominates view counts. The model fits known channels very well but struggles to generalize to unseen channels — an inherent challenge since channel audience size varies by orders of magnitude.

### 3.5 Inference

The `RankerModel` class in `app/models/ranker.py` wraps the trained model for inference.

**Key method**: `predict_video(video, yt_stats, title_embedding) -> dict`

```python
# Returns:
{
    "label": "standard",              # failed | standard | successful | viral
    "predicted_log_views": 7.12,      # raw model output
    "predicted_views": 1236.5,        # exp(7.12) - 1
}
```

Classification is derived from regression output using percentile thresholds (p25/p75/p95) stored in model metadata.

---

## 4. Discovery Pipeline (LLM Feature)

The discovery pipeline (`app/discovery/`) automates finding YouTube videos worth transporting to Bilibili. It answers: "What's trending on Bilibili right now, and which English YouTube videos match those trends?"

### 4.1 Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     DiscoveryPipeline.run()                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Step 1: Fetch Bilibili Trending Keywords                       │
│  ┌──────────────────────────────┐                               │
│  │ trending.py                  │  Bilibili Hot Search API      │
│  │ fetch_trending_keywords()    │──→ 10 keywords + heat scores  │
│  │ Filters out commercial ads   │                               │
│  └──────────────┬───────────────┘                               │
│                 │                                                │
│  Step 2a: Translate to English (LLM)                            │
│  ┌──────────────▼───────────────┐                               │
│  │ llm_scorer.py                │  Ollama + Qwen 2.5:7b         │
│  │ translate_keyword()          │──→ 2-3 English search queries  │
│  │ Chinese keyword → English    │    per keyword                 │
│  └──────────────┬───────────────┘                               │
│                 │                                                │
│  Step 2b: Search YouTube                                        │
│  ┌──────────────▼───────────────┐                               │
│  │ youtube_search.py            │  YouTube Data API v3           │
│  │ search_youtube_videos()      │──→ Up to 5 videos per query   │
│  │ publishedAfter filter (30d)  │    with full stats             │
│  └──────────────┬───────────────┘                               │
│                 │                                                │
│  Step 3: Filter Already-Seen                                    │
│  ┌──────────────▼───────────────┐                               │
│  │ database.py                  │  Check competitor_videos +     │
│  │ get_already_transported_     │  discovery_recommendations     │
│  │   yt_ids()                   │──→ Skip duplicates             │
│  └──────────────┬───────────────┘                               │
│                 │                                                │
│  Step 4: Score Relevance (LLM)                                  │
│  ┌──────────────▼───────────────┐                               │
│  │ llm_scorer.py                │  Ollama + Qwen 2.5:7b         │
│  │ score_relevance()            │──→ 0.0-1.0 score + reasoning  │
│  │ Filter: score >= 0.5         │                               │
│  └──────────────┬───────────────┘                               │
│                 │                                                │
│  Step 5: Predict Bilibili Views (ML)                            │
│  ┌──────────────▼───────────────┐                               │
│  │ ranker.py                    │  Trained LightGBM model        │
│  │ predict_video()              │──→ predicted views + label     │
│  └──────────────┬───────────────┘                               │
│                 │                                                │
│  Step 6: Rank & Save                                            │
│  ┌──────────────▼───────────────┐                               │
│  │ Combined score formula:      │                               │
│  │  0.2 × heat + 0.4 × rel     │  Saved to discovery_runs +    │
│  │  + 0.4 × predicted_views    │  discovery_recommendations     │
│  └──────────────────────────────┘                               │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 Module Details

#### `trending.py` — Bilibili Hot Search

Calls `bilibili_api.search.get_hot_search_keywords()` to fetch the current top trending keywords. Each keyword has a `heat_score` (typically 100K–5M) and a commercial flag. Commercial entries are filtered out.

**API response format** (top-level `list`, not nested):
```json
{
  "list": [
    {
      "keyword": "...",
      "heat_score": 5000000,
      "pos": 1,
      "stat_datas": { "is_commercial": "0" }
    }
  ]
}
```

#### `llm_scorer.py` — LLM Translation & Relevance Scoring

Uses Ollama's Python SDK to communicate with a locally-running Qwen 2.5:7b model. Two functions:

**`translate_keyword(keyword)`**: Converts a Chinese keyword to 2–3 English YouTube search queries. Uses Pydantic's `model_json_schema()` as the Ollama `format` parameter for structured JSON output. Example:

```
Input:  "立陶宛为何承认犯错"
Output: ["Why did Lithuania admit it made a mistake",
         "Lithuania's apology for past actions"]
Topic:  "Geopolitical issue involving Lithuania acknowledging diplomatic mistakes"
```

**`score_relevance(keyword, video)`**: Rates how relevant a YouTube video is to a Bilibili keyword (0.0–1.0). The prompt asks the LLM to consider topic match, audience overlap, and transport suitability. Videos scoring below 0.5 are filtered out.

#### `youtube_search.py` — YouTube API Search

Two-step API call:
1. `search.list` (100 quota units) — finds video IDs matching the query
2. `videos.list` (1 unit per 50 videos) — fetches full stats (views, likes, duration, category, tags)

**Recency filter**: The `publishedAfter` parameter limits results to videos from the last N days (default 30). This ensures only fresh content is recommended.

Returns `YouTubeCandidate` objects with all metadata needed for ML prediction and relevance scoring.

#### `pipeline.py` — Orchestrator

The `DiscoveryPipeline` class coordinates all steps. Key features:

- **Deduplication**: Before processing, loads all YouTube video IDs from `competitor_videos` (already transported) and `discovery_recommendations` (previously recommended). Skips any matches.
- **Lazy ranker loading**: The ML model is only loaded on first use (may not exist yet).
- **Combined score formula**:
  ```
  score = 0.2 × norm_heat + 0.4 × relevance + 0.4 × norm_predicted_views
  ```
  Where `norm_heat = log1p(heat) / log1p(5M)` and `norm_views = log1p(views) / log1p(1M)`.

### 4.3 Data Models

Defined in `app/discovery/models.py`:

| Model | Type | Purpose |
|-------|------|---------|
| `TrendingKeyword` | dataclass | Bilibili keyword with heat score, position, commercial flag |
| `TranslatedKeyword` | Pydantic | LLM output: English search queries + topic summary |
| `YouTubeCandidate` | dataclass | YouTube video with full stats (id, title, views, likes, duration, tags, etc.) |
| `RelevanceResult` | Pydantic | LLM output: relevance score, reasoning, detected topics |
| `Recommendation` | dataclass | Final ranked result combining all signals |

Pydantic models are used for LLM outputs because Ollama's `format` parameter accepts JSON Schema for structured output enforcement.

---

## 5. CLI API

All commands use: `python -m app.cli --db-path <path> [--json] <command> [options]`

The `--json` flag outputs machine-readable JSON instead of formatted text.

### 5.1 Data Collection Commands

| Command | Description | Key Options |
|---------|-------------|-------------|
| `add-competitor <uid>` | Register a Bilibili channel to monitor | — |
| `list-competitors` | List all tracked channels | — |
| `collect-competitor <uid>` | Scrape videos from one channel | `--count` (default: 100) |
| `collect-all-competitors` | Scrape videos from all active channels | `--count` (default: 100) |

### 5.2 Labeling Commands

| Command | Description | Key Options |
|---------|-------------|-------------|
| `label-videos` | Auto-label competitor videos by view percentiles | `--relabel`, `--limit` (default: 1000) |
| `training-status` | Show label distribution summary | — |

### 5.3 Upload Tracking Commands

| Command | Description | Key Options |
|---------|-------------|-------------|
| `track` | Collect performance metrics at checkpoints | `--checkpoint {1,6,24,48,168,720}` |
| `label` | Auto-label uploads based on performance | `--min-checkpoint` (default: 168) |
| `stats` | Show upload statistics summary | — |

### 5.4 ML Training Command

| Command | Description | Key Options |
|---------|-------------|-------------|
| `train` | Train LightGBM scoring model | `--model-dir`, `--num-rounds` (500), `--learning-rate`, `--min-samples` (50), `--gpu` / `--no-gpu` |

Output includes validation results, class distribution, and evaluation metrics (accuracy, F1, log loss).

### 5.5 Discovery Pipeline Commands

| Command | Description | Key Options |
|---------|-------------|-------------|
| `discover` | Run full discovery pipeline | `--max-keywords` (10), `--videos-per-keyword` (5), `--max-age-days` (30), `--llm-model` (qwen2.5:7b), `--model-dir` |
| `discover-trending` | Fetch and display current Bilibili trending keywords | — |
| `discover-history` | Show past discovery run results | `--limit` (5) |

**Example output** (`discover`):
```
Recommendations: 14

  #1 [0.7234] Lithuania CRAWLS back to China
     Keyword: 立陶宛为何承认犯错 (heat=685,516)
     Channel: The Bridge Geo
     YT views: 42,067 | Relevance: 0.90
     Predicted: 688 views (standard)
```

---

## 6. Test Suite

20 test files, organized by module. Run with: `pytest tests/ -v`

### 6.1 Discovery Tests (`test_discovery.py` — 20 tests)

| Class | Tests | What It Covers |
|-------|-------|----------------|
| `TestTrendingKeyword` | 2 | Dataclass creation, commercial flag |
| `TestYouTubeCandidate` | 1 | Dataclass creation with all fields |
| `TestRelevanceResult` | 2 | Pydantic model creation, JSON roundtrip serialization |
| `TestRecommendation` | 2 | Dataclass creation, nullable prediction fields |
| `TestDiscoveryDB` | 4 | Table creation idempotency, save/get runs, save recommendations, empty history |
| `TestFetchTrending` | 2 | Commercial keyword filtering (mocked API), empty response handling |
| `TestYouTubeSearch` | 2 | ISO 8601 duration parsing, full search flow (mocked HTTP) |
| `TestLLMScorer` | 2 | Relevance scoring (mocked Ollama), error handling returns None |
| `TestPipelineCombinedScore` | 3 | Score computation with full inputs, without prediction, with zero heat |

All external dependencies (Bilibili API, YouTube API, Ollama) are mocked in tests.

### 6.2 ML Pipeline Tests

| File | Tests | What It Covers |
|------|-------|----------------|
| `test_features.py` | 33 | All 43 features: duration buckets, safe ratios, cyclical time encoding, YouTube stats with/without imputation, clickbait detection, title embeddings, feature name ordering |
| `test_evaluator.py` | 10 | Regression report fields, accuracy bounds, feature importance, JSON serialization, summary string formatting |
| `test_trainer.py` | 6 | Synthetic end-to-end training, model file output, CV metadata, insufficient data handling, custom learning rate |
| `test_ranker.py` | 13 | Model load/save roundtrip, single video prediction, batch prediction, unseen channel handling, imputation, classification thresholds |
| `test_data_validator.py` | 10 | Validation rules: empty data, too few samples, single class, custom minimums, class distribution, warning thresholds |

### 6.3 Collector & Infrastructure Tests

| File | Tests | What It Covers |
|------|-------|----------------|
| `test_database.py` | 17 | Connection, upload tracking CRUD, performance upsert, outcome labels, checkpoint filtering, error handling |
| `test_bilibili_tracker.py` | 17 | Metrics calculation (velocity, engagement), label determination (all 4 labels with edge cases), rate limiter |
| `test_competitor_monitor.py` | 13 | YouTube source ID extraction (brackets, URLs, yt: prefix), channel CRUD, video save/update, label management |
| `test_labeler.py` | ~12 | Labeling logic, relabeling, batch operations |
| `test_cli.py` | 17 | Argument parsing for all commands, result formatting, missing required args |

### 6.4 Test Coverage Summary

| Area | Test Count | Status |
|------|-----------|--------|
| Discovery pipeline | 20 | All passing |
| Feature extraction | 33 | All passing |
| Model training & evaluation | 29 | All passing |
| Database operations | 17 | All passing |
| Collectors & labeling | ~42 | 40 passing, 2 pre-existing failures (rate_limiter timing) |
| CLI parsing | 17 | All passing |
| **Total** | **~158** | **~156 passing** |

---

## 7. End-to-End Workflow

### 7.1 Initial Setup (One-Time)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Register competitor channels
python -m app.cli --db-path data.db add-competitor 12345678

# 3. Collect their videos
python -m app.cli --db-path data.db collect-all-competitors

# 4. Enrich with YouTube stats
python enrich_youtube.py

# 5. Label videos
python -m app.cli --db-path data.db label-videos

# 6. Train the ML model
python -m app.cli --db-path data.db train
```

### 7.2 Daily Discovery Run

```bash
# Start Ollama with Qwen model
ollama serve &
ollama pull qwen2.5:7b

# Run discovery (finds trending topics, searches YouTube, scores & ranks)
python -m app.cli --db-path data.db discover \
    --max-keywords 10 \
    --videos-per-keyword 5 \
    --max-age-days 30

# Review past results
python -m app.cli --db-path data.db discover-history --limit 5
```

### 7.3 Pipeline Flow (Single Run)

```
Bilibili Hot Search API
        │
        ▼
10 Chinese trending keywords (filtered, non-commercial)
        │
        ▼  LLM: translate_keyword()
20-30 English search queries
        │
        ▼  YouTube Data API (publishedAfter=30 days)
~100 YouTube candidates (deduplicated across queries)
        │
        ▼  DB lookup: skip already-transported / previously-recommended
~95 new candidates
        │
        ▼  LLM: score_relevance() — filter < 0.5
~15 relevant candidates
        │
        ▼  ML: ranker.predict_video()
~15 predictions with view counts and labels
        │
        ▼  Combined score: 0.2×heat + 0.4×relevance + 0.4×views
Ranked recommendations saved to DB
```

**Typical runtime**: ~5 minutes for 10 keywords (dominated by LLM calls at ~2s each).

---

## 8. Dependencies

```
# Core
bilibili-api-python>=16.0.0    # Bilibili API client
httpx>=0.24.0                   # HTTP client for YouTube API
ollama>=0.4.0                   # Ollama LLM client
pydantic>=2.0.0                 # Structured LLM output validation

# ML
numpy>=1.24.0
pandas>=2.0.0
scikit-learn>=1.3.0             # GroupKFold, metrics
lightgbm>=4.1.0                 # Gradient boosting
joblib>=1.3.0                   # Model serialization

# Database
aiosqlite>=0.19.0               # Async SQLite
asyncpg>=0.28.0                 # PostgreSQL (planned)

# Testing
pytest>=7.4.0
pytest-asyncio>=0.21.0
```

**External services**:
- Ollama (local): Must be running with `qwen2.5:7b` model pulled
- YouTube Data API: API key in `youtube_search.py` (100 search quota units per query)
- Bilibili API: No auth required for hot search endpoint
