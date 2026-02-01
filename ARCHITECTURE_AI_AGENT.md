# Great Transport v2: ML-Powered Video Selection System

## Overview

A hybrid Go + Python system that combines rule-based filtering, machine learning scoring, and optional LLM review to intelligently select YouTube videos for cross-platform upload to Bilibili/TikTok. The system learns from historical performance data to continuously improve selection accuracy.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                              GREAT TRANSPORT v2                                          │
│                         Intelligent Video Selection System                               │
└─────────────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                 DATA COLLECTION LAYER                                    │
├─────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                          │
│  ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐  │
│  │  YouTube        │   │  Bilibili       │   │  Competitor     │   │  Performance    │  │
│  │  Scanner        │   │  Tracker        │   │  Monitor        │   │  Tracker        │  │
│  │  (Go + yt-dlp)  │   │  (Python)       │   │  (Python)       │   │  (Python)       │  │
│  │                 │   │                 │   │                 │   │                 │  │
│  │  • Channel scan │   │  • Track our    │   │  • Find top     │   │  • Track views  │  │
│  │  • Metadata     │   │    uploads      │   │    transporters │   │    over time    │  │
│  │  • Thumbnails   │   │  • Coins/Likes  │   │  • Their video  │   │  • 1h/24h/7d    │  │
│  │                 │   │  • Danmaku      │   │    performance  │   │  • Label data   │  │
│  └────────┬────────┘   └────────┬────────┘   └────────┬────────┘   └────────┬────────┘  │
│           │                     │                     │                     │           │
└───────────┼─────────────────────┼─────────────────────┼─────────────────────┼───────────┘
            │                     │                     │                     │
            ▼                     ▼                     ▼                     ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                           SHARED DATABASE (PostgreSQL + pgvector)                        │
├─────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                          │
│  ┌──────────────────────────────────────┐  ┌──────────────────────────────────────────┐ │
│  │  Core Tables                         │  │  ML Tables                               │ │
│  │  ─────────────────────────────────── │  │  ──────────────────────────────────────  │ │
│  │  • channels                          │  │  • upload_performance (time-series)      │ │
│  │  • video_candidates (+embeddings)    │  │  • upload_outcomes (labels)              │ │
│  │  • filter_rules                      │  │  • competitor_channels                   │ │
│  │  • rule_decisions                    │  │  • competitor_videos                     │ │
│  │  • uploads (+bilibili_bvid)          │  │  • ml_predictions                        │ │
│  │                                      │  │  • feature_store                         │ │
│  │                                      │  │  • model_registry                        │ │
│  └──────────────────────────────────────┘  └──────────────────────────────────────────┘ │
│                                                                                          │
│  ┌──────────────────────────────────────────────────────────────────────────────────────┐│
│  │  Vector Storage (pgvector)                                                           ││
│  │  • title_embedding vector(768)       • thumbnail_embedding vector(512)              ││
│  │  • description_embedding vector(768) • similarity search indexes                    ││
│  └──────────────────────────────────────────────────────────────────────────────────────┘│
│                                                                                          │
└─────────────────────────────────────────────────────────────────────────────────────────┘
                    │                                               │
                    ▼                                               ▼
┌───────────────────────────────────────────┐   ┌───────────────────────────────────────────┐
│         GO BACKEND (Orchestration)         │   │       PYTHON ML SERVICE (FastAPI)         │
├───────────────────────────────────────────┤   ├───────────────────────────────────────────┤
│                                            │   │                                           │
│  ┌──────────────────────────────────────┐ │   │  ┌─────────────────────────────────────┐  │
│  │  HTTP API                            │ │   │  │  Embedding Service                  │  │
│  │  • POST /scan                        │ │   │  │  • CLIP (thumbnails)                │  │
│  │  • POST /filter                      │ │   │  │  • DistilBERT (text)                │  │
│  │  • POST /select (→ ML service)       │ │   │  │  • Batch processing                 │  │
│  │  • POST /upload                      │ │   │  └─────────────────────────────────────┘  │
│  │  • GET  /stats                       │ │   │                                           │
│  └──────────────────────────────────────┘ │   │  ┌─────────────────────────────────────┐  │
│                                            │   │  │  Ranking Model                      │  │
│  ┌──────────────────────────────────────┐ │   │  │  • LightGBM / XGBoost               │  │
│  │  Selection Pipeline                  │ │   │  │  • 50+ features                     │  │
│  │                                      │ │   │  │  • Predicts success probability     │  │
│  │  Stage 1: Rule Engine (Go)           │ │   │  └─────────────────────────────────────┘  │
│  │      ↓                               │ │   │                                           │
│  │  Stage 2: ML Scoring ─────────────────────►│  ┌─────────────────────────────────────┐  │
│  │      ↓              gRPC/REST        │ │   │  │  Training Pipeline                  │  │
│  │  Stage 3: LLM Refinement (optional)  │ │   │  │  • Feature engineering              │  │
│  │      ↓                               │ │   │  │  • Scheduled retraining             │  │
│  │  Final Selection                     │ │   │  │  • Model evaluation & A/B testing   │  │
│  └──────────────────────────────────────┘ │   │  └─────────────────────────────────────┘  │
│                                            │   │                                           │
│  ┌──────────────────────────────────────┐ │   │  ┌─────────────────────────────────────┐  │
│  │  External Tools                      │ │   │  │  Data Collectors                    │  │
│  │  • yt-dlp (download)                 │ │   │  │  • Bilibili performance tracker     │  │
│  │  • biliup (upload)                   │ │   │  │  • Competitor channel monitor       │  │
│  │  • Ollama (optional LLM)             │ │   │  │  • Outcome labeler                  │  │
│  └──────────────────────────────────────┘ │   │  └─────────────────────────────────────┘  │
│                                            │   │                                           │
└───────────────────────────────────────────┘   └───────────────────────────────────────────┘
                    │                                               │
                    └───────────────────────┬───────────────────────┘
                                            ▼
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                                    FEEDBACK LOOP                                         │
├─────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                          │
│   ┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐           │
│   │  Upload     │ ──► │  Track      │ ──► │  Label      │ ──► │  Retrain    │           │
│   │  to Bilibili│     │  Performance│     │  Outcome    │     │  Model      │           │
│   │             │     │  (1h-30d)   │     │             │     │             │           │
│   └─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘           │
│                                                                                          │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## Component Details

### 1. Data Collection Layer

#### YouTube Scanner (Go + yt-dlp)
```
┌────────────────────────────────────────────────────────┐
│                  YOUTUBE SCANNER                        │
├────────────────────────────────────────────────────────┤
│  Input: Channel IDs (configured watchlist)             │
│  Method: yt-dlp --flat-playlist + --dump-json          │
│  Output: Video metadata + thumbnails                   │
│  Frequency: Every 1-6 hours (configurable)             │
│                                                        │
│  Computed Metrics:                                     │
│  • view_velocity = views / hours_since_publish         │
│  • engagement_rate = (likes + comments) / views        │
└────────────────────────────────────────────────────────┘
```

#### Bilibili Performance Tracker (Python)
```
┌────────────────────────────────────────────────────────┐
│              BILIBILI PERFORMANCE TRACKER              │
├────────────────────────────────────────────────────────┤
│  Purpose: Track our uploaded videos' performance       │
│  Library: bilibili-api-python                          │
│                                                        │
│  Metrics Collected:                                    │
│  • views, likes, coins, favorites, shares              │
│  • danmaku (bullet comments), comments                 │
│                                                        │
│  Tracking Schedule:                                    │
│  • 1 hour after upload                                 │
│  • 6 hours after upload                                │
│  • 24 hours after upload                               │
│  • 48 hours after upload                               │
│  • 7 days after upload                                 │
│  • 30 days after upload                                │
└────────────────────────────────────────────────────────┘
```

#### Competitor Monitor (Python)
```
┌────────────────────────────────────────────────────────┐
│                COMPETITOR MONITOR                       │
├────────────────────────────────────────────────────────┤
│  Purpose: Learn from successful transporters           │
│                                                        │
│  Data Collected:                                       │
│  • Top transporter channels on Bilibili/TikTok         │
│  • Their video selection patterns                      │
│  • Performance metrics of their uploads                │
│  • Source-to-transported video mapping                 │
│                                                        │
│  Uses:                                                 │
│  • Training data for ML model                          │
│  • Benchmark for success metrics                       │
│  • Feature: similar_video_performance                  │
└────────────────────────────────────────────────────────┘
```

### 2. Decision Pipeline (Three Stages)

```
┌────────────────────────────────────────────────────────┐
│              STAGE 1: RULE-BASED FILTER (Go)           │
├────────────────────────────────────────────────────────┤
│                                                        │
│  Rule Types:                                           │
│  ├─ min: Minimum threshold (e.g., min_views=1000)     │
│  ├─ max: Maximum threshold (e.g., max_duration=3600)  │
│  ├─ blocklist: Reject if in list                      │
│  ├─ allowlist: Accept only if in list                 │
│  ├─ regex: Reject if pattern matches                  │
│  └─ age_days: Max age since publish                   │
│                                                        │
│  Output: PASS (to stage 2) or REJECT (with reason)    │
│  Implementation: internal/app/rules.go                 │
│                                                        │
└────────────────────────────────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────┐
│            STAGE 2: ML SCORING MODEL (Python)          │
├────────────────────────────────────────────────────────┤
│                                                        │
│  Model: LightGBM / XGBoost                             │
│  Training: Historical upload performance data          │
│  Output: Score 0.0 - 1.0 (predicted success)          │
│                                                        │
│  Feature Categories (50+ features):                    │
│  ┌─────────────────────────────────────────────────┐  │
│  │ Content Features                                │  │
│  │ • duration_bucket (short/medium/long)           │  │
│  │ • title_length, has_question_mark, has_number   │  │
│  │ • category_encoded, language_encoded            │  │
│  ├─────────────────────────────────────────────────┤  │
│  │ Engagement Features (from source)               │  │
│  │ • view_velocity (views per hour)                │  │
│  │ • like_ratio, comment_ratio, engagement_rate    │  │
│  ├─────────────────────────────────────────────────┤  │
│  │ Time Features                                   │  │
│  │ • upload_hour, upload_day_of_week               │  │
│  │ • days_since_publish                            │  │
│  ├─────────────────────────────────────────────────┤  │
│  │ Embedding Features (768d text, 512d image)      │  │
│  │ • similar_video_avg_performance                 │  │
│  │ • category_avg_performance                      │  │
│  ├─────────────────────────────────────────────────┤  │
│  │ Channel Features                                │  │
│  │ • channel_avg_engagement                        │  │
│  │ • channel_upload_frequency                      │  │
│  └─────────────────────────────────────────────────┘  │
│                                                        │
│  Selection: top_k candidates OR score > threshold     │
│                                                        │
└────────────────────────────────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────┐
│         STAGE 3: LLM AGENT REVIEW (Optional)           │
├────────────────────────────────────────────────────────┤
│                                                        │
│  Provider: Ollama (local) or Claude API                │
│  Models: mistral:7b, qwen2:7b, llama3:8b              │
│                                                        │
│  Evaluation Criteria:                                  │
│  ├─ Content appropriateness for target platform       │
│  ├─ Cultural fit for Chinese audience                 │
│  ├─ Potential copyright/legal issues                  │
│  └─ Spam/low-quality content detection                │
│                                                        │
│  Output:                                               │
│  {                                                     │
│    "decision": "upload|skip|defer",                   │
│    "confidence": 0.0-1.0,                             │
│    "reasoning": "...",                                │
│    "suggested_title_zh": "...",                       │
│    "risk_flags": ["...", "..."]                       │
│  }                                                     │
│                                                        │
└────────────────────────────────────────────────────────┘
```

### 3. Feedback Loop

```
┌────────────────────────────────────────────────────────┐
│                    FEEDBACK LOOP                        │
├────────────────────────────────────────────────────────┤
│                                                        │
│  1. UPLOAD                                             │
│     Video uploaded to Bilibili via biliup              │
│     Store: bilibili_bvid, predicted_score              │
│                                                        │
│  2. TRACK PERFORMANCE                                  │
│     Collect metrics at: 1h, 6h, 24h, 48h, 7d, 30d     │
│     Metrics: views, likes, coins, favorites, shares    │
│                                                        │
│  3. LABEL OUTCOME                                      │
│     ┌─────────────────────────────────────────────┐   │
│     │ Label      │ Criteria (Bilibili)            │   │
│     ├────────────┼────────────────────────────────┤   │
│     │ viral      │ >1M views, >5% ER, coins >10K  │   │
│     │ successful │ >100K views, >3% ER            │   │
│     │ standard   │ >10K views, 1-3% ER            │   │
│     │ failed     │ <10K views or <1% ER           │   │
│     └─────────────────────────────────────────────┘   │
│                                                        │
│  4. RETRAIN MODEL                                      │
│     • Add labeled data to training set                │
│     • Trigger retraining (scheduled or threshold)     │
│     • A/B test new model vs current                   │
│     • Promote if improved                             │
│                                                        │
└────────────────────────────────────────────────────────┘
```

---

## Database Schema

### Core Tables (Enhanced)

```sql
-- Enable vector extension (PostgreSQL)
CREATE EXTENSION IF NOT EXISTS vector;

-- Channels to monitor (existing, unchanged)
CREATE TABLE channels (
    channel_id TEXT PRIMARY KEY,
    name TEXT,
    url TEXT NOT NULL,
    subscriber_count INTEGER,
    video_count INTEGER,
    last_scanned_at TIMESTAMP,
    scan_frequency_hours INTEGER DEFAULT 6,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Video candidates (enhanced with embeddings)
CREATE TABLE video_candidates (
    video_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL REFERENCES channels(channel_id),
    title TEXT,
    description TEXT,
    duration_seconds INTEGER,
    view_count INTEGER,
    like_count INTEGER,
    comment_count INTEGER,
    published_at TIMESTAMP,
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    thumbnail_url TEXT,
    tags TEXT,  -- JSON array
    category TEXT,
    language TEXT,

    -- Computed metrics
    view_velocity REAL,
    engagement_rate REAL,

    -- ML fields (NEW)
    title_embedding vector(768),
    thumbnail_embedding vector(512),
    description_embedding vector(768),
    ml_score REAL,
    ml_label TEXT
);

CREATE INDEX idx_candidates_title_emb ON video_candidates
    USING ivfflat (title_embedding vector_cosine_ops);

-- Filter rules (existing, unchanged)
CREATE TABLE filter_rules (
    id SERIAL PRIMARY KEY,
    rule_name TEXT NOT NULL UNIQUE,
    rule_type TEXT NOT NULL,
    field TEXT NOT NULL,
    value TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    priority INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Rule decisions (existing, unchanged)
CREATE TABLE rule_decisions (
    id SERIAL PRIMARY KEY,
    video_id TEXT NOT NULL REFERENCES video_candidates(video_id),
    rule_passed BOOLEAN NOT NULL,
    reject_rule_name TEXT,
    reject_reason TEXT,
    evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Uploads (enhanced)
CREATE TABLE uploads (
    id SERIAL PRIMARY KEY,
    video_id TEXT NOT NULL UNIQUE REFERENCES video_candidates(video_id),
    channel_id TEXT NOT NULL,

    -- Platform info
    platform TEXT NOT NULL DEFAULT 'bilibili',
    bilibili_bvid TEXT,  -- NEW: Bilibili video ID

    -- Status
    uploaded_at TIMESTAMP NOT NULL,
    upload_status TEXT DEFAULT 'pending',
    error_message TEXT,

    -- Source metrics at upload time (NEW)
    source_views INTEGER,
    source_likes INTEGER,
    source_comments INTEGER,

    -- Prediction at upload time (NEW)
    predicted_score REAL,
    predicted_label TEXT
);
```

### ML Tables (New)

```sql
-- Time-series performance tracking
CREATE TABLE upload_performance (
    id SERIAL PRIMARY KEY,
    upload_id INTEGER NOT NULL REFERENCES uploads(id),
    measured_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    hours_since_upload INTEGER NOT NULL,

    -- Bilibili metrics
    views INTEGER,
    likes INTEGER,
    coins INTEGER,
    favorites INTEGER,
    shares INTEGER,
    danmaku INTEGER,
    comments INTEGER,

    -- Derived
    view_velocity REAL,
    engagement_rate REAL,

    UNIQUE (upload_id, hours_since_upload)
);

CREATE INDEX idx_perf_upload ON upload_performance(upload_id);

-- Final outcome labels for training
CREATE TABLE upload_outcomes (
    id SERIAL PRIMARY KEY,
    upload_id INTEGER NOT NULL UNIQUE REFERENCES uploads(id),

    final_views INTEGER,
    final_engagement_rate REAL,

    success_label TEXT,  -- viral, successful, standard, failed
    success_score REAL,  -- 0.0 - 1.0

    labeled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Competitor channels to learn from
CREATE TABLE competitor_channels (
    id SERIAL PRIMARY KEY,
    platform TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    channel_name TEXT,
    follower_count INTEGER,
    avg_views INTEGER,
    avg_engagement_rate REAL,
    content_focus TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (platform, channel_id)
);

-- Competitor videos (training data source)
CREATE TABLE competitor_videos (
    id SERIAL PRIMARY KEY,
    competitor_channel_id INTEGER NOT NULL REFERENCES competitor_channels(id),
    platform TEXT NOT NULL,
    video_id TEXT NOT NULL,
    source_video_id TEXT,  -- Original YouTube ID if identifiable

    title TEXT,
    duration_seconds INTEGER,
    uploaded_at TIMESTAMP,

    views INTEGER,
    likes INTEGER,
    coins INTEGER,
    favorites INTEGER,
    shares INTEGER,
    comments INTEGER,

    engagement_rate REAL,
    success_label TEXT,
    title_embedding vector(768),

    collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (platform, video_id)
);

-- ML predictions log
CREATE TABLE ml_predictions (
    id SERIAL PRIMARY KEY,
    video_id TEXT NOT NULL REFERENCES video_candidates(video_id),
    model_version TEXT NOT NULL,

    score REAL,
    label TEXT,
    confidence REAL,
    top_features JSONB,  -- For explainability

    predicted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Feature store (precomputed features)
CREATE TABLE feature_store (
    id SERIAL PRIMARY KEY,
    video_id TEXT NOT NULL UNIQUE REFERENCES video_candidates(video_id),

    -- Content features
    f_duration_bucket TEXT,
    f_title_length INTEGER,
    f_has_question_mark BOOLEAN,
    f_has_number BOOLEAN,
    f_category_encoded INTEGER,
    f_language_encoded INTEGER,

    -- Engagement features
    f_view_velocity REAL,
    f_like_ratio REAL,
    f_comment_ratio REAL,
    f_engagement_rate REAL,

    -- Time features
    f_upload_hour INTEGER,
    f_upload_day_of_week INTEGER,
    f_days_since_publish INTEGER,

    -- Similarity features
    f_similar_video_avg_performance REAL,
    f_category_avg_performance REAL,

    -- Channel features
    f_channel_avg_engagement REAL,
    f_channel_upload_frequency REAL,

    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Model registry
CREATE TABLE model_registry (
    id SERIAL PRIMARY KEY,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    model_type TEXT,

    train_accuracy REAL,
    val_accuracy REAL,
    test_accuracy REAL,
    auc_roc REAL,

    training_samples INTEGER,
    feature_count INTEGER,
    hyperparameters JSONB,

    model_path TEXT,
    is_active BOOLEAN DEFAULT FALSE,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE (model_name, model_version)
);
```

---

## Project Structure

```
great-transport/
├── great-transport-rework-backend/          # Go Backend
│   ├── cmd/yttransfer/
│   │   └── main.go                          # CLI entry point
│   ├── internal/app/
│   │   ├── store.go                         # Database operations
│   │   ├── repository.go                    # Data models
│   │   ├── rules.go                         # Rule engine (Stage 1)
│   │   ├── downloader.go                    # yt-dlp wrapper
│   │   ├── uploader_biliup.go              # biliup wrapper
│   │   ├── scanner.go                       # YouTube channel scanner
│   │   ├── controller.go                    # Sync orchestration
│   │   ├── http.go                          # HTTP API
│   │   ├── ml_client.go                     # Client to Python ML service
│   │   ├── selector.go                      # Selection pipeline orchestrator
│   │   └── stats.go                         # Statistics queries
│   ├── go.mod
│   └── Dockerfile
│
├── ml-service/                              # Python ML Service
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                          # FastAPI application
│   │   ├── config.py
│   │   ├── database.py                      # SQLAlchemy models
│   │   │
│   │   ├── api/
│   │   │   ├── embeddings.py                # POST /api/v1/embeddings
│   │   │   ├── predictions.py               # POST /api/v1/predictions
│   │   │   └── training.py                  # POST /api/v1/training/trigger
│   │   │
│   │   ├── services/
│   │   │   ├── embedding_service.py         # CLIP + DistilBERT
│   │   │   ├── feature_service.py           # Feature engineering
│   │   │   ├── prediction_service.py        # Model inference
│   │   │   └── training_service.py          # Model training
│   │   │
│   │   ├── models/
│   │   │   ├── ranker.py                    # LightGBM/XGBoost wrapper
│   │   │   └── embedder.py                  # CLIP/BERT wrappers
│   │   │
│   │   └── collectors/
│   │       ├── bilibili_tracker.py          # Track our uploads
│   │       ├── competitor_monitor.py        # Monitor competitors
│   │       └── labeler.py                   # Auto-label outcomes
│   │
│   ├── training/
│   │   ├── train_ranker.py                  # Training script
│   │   ├── evaluate_model.py
│   │   └── feature_engineering.py
│   │
│   ├── notebooks/                           # Jupyter notebooks
│   │   ├── 01_data_exploration.ipynb
│   │   ├── 02_feature_analysis.ipynb
│   │   └── 03_model_experiments.ipynb
│   │
│   ├── requirements.txt
│   ├── Dockerfile
│   └── pyproject.toml
│
├── docker-compose.yml                       # Full stack deployment
├── Makefile
└── README.md
```

---

## Technology Stack

### Go Backend
| Component | Technology | Purpose |
|-----------|------------|---------|
| HTTP Framework | net/http | REST API |
| Database Driver | pgx | PostgreSQL connection |
| CLI | flag (stdlib) | Command-line interface |
| Download | yt-dlp | YouTube video/metadata |
| Upload | biliup | Bilibili upload |
| LLM (optional) | Ollama API | Content review |

### Python ML Service
| Component | Technology | Purpose |
|-----------|------------|---------|
| Web Framework | FastAPI | REST API |
| ML Training | LightGBM, XGBoost | Ranking model |
| Text Embeddings | transformers (DistilBERT) | Title/description vectors |
| Image Embeddings | transformers (CLIP) | Thumbnail vectors |
| Database | SQLAlchemy + asyncpg | PostgreSQL ORM |
| Bilibili API | bilibili-api-python | Performance tracking |
| Task Queue | Celery (optional) | Background jobs |

### Infrastructure
| Component | Technology | Purpose |
|-----------|------------|---------|
| Database | PostgreSQL 16 + pgvector | Data + vector storage |
| Containerization | Docker Compose | Development/deployment |
| LLM Server | Ollama | Local LLM inference |
| Monitoring | Prometheus + Grafana | Metrics |
| Analytics | Metabase | Business dashboards |

---

## API Endpoints

### Go Backend API

```
POST /scan
  Body: {"channel_id": "UC...", "limit": 10}
  → Scan channel, store candidates

POST /filter
  Body: {"limit": 100}
  → Run rule filter on pending candidates

POST /select
  Body: {"limit": 20}
  → Run full selection pipeline (rules → ML → optional LLM)

POST /upload
  Body: {"video_ids": ["vid1", "vid2"]}
  → Download and upload selected videos

GET /stats
  → Return statistics (candidates, filtered, uploaded, performance)

GET /health
  → Health check
```

### Python ML Service API

```
POST /api/v1/embeddings/generate
  Body: {"video_id": "...", "title": "...", "thumbnail_url": "..."}
  → Generate and store embeddings

POST /api/v1/predictions/score
  Body: {"video_id": "..."}
  → Return ML prediction score and label

POST /api/v1/predictions/batch
  Body: {"video_ids": ["vid1", "vid2", ...]}
  → Batch scoring

POST /api/v1/training/trigger
  Body: {"model_name": "ranker", "min_samples": 1000}
  → Trigger model retraining

GET /api/v1/models/active
  → Return currently active model info

GET /health
  → Health check
```

---

## Docker Compose

```yaml
version: '3.8'

services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: transport
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: great_transport
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"

  go-backend:
    build: ./great-transport-rework-backend
    depends_on:
      - postgres
      - ml-service
    environment:
      DATABASE_URL: postgres://transport:${POSTGRES_PASSWORD}@postgres:5432/great_transport
      ML_SERVICE_URL: http://ml-service:8000
    ports:
      - "8080:8080"
    volumes:
      - ./downloads:/app/downloads
      - ./cookies.json:/app/cookies.json

  ml-service:
    build: ./ml-service
    depends_on:
      - postgres
    environment:
      DATABASE_URL: postgres://transport:${POSTGRES_PASSWORD}@postgres:5432/great_transport
    ports:
      - "8000:8000"
    volumes:
      - ./ml-service/models:/app/models
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]

  ollama:
    image: ollama/ollama:latest
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama
    profiles:
      - llm  # Optional, enable with --profile llm

volumes:
  postgres_data:
  ollama_data:
```

---

## Data Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           DATA FLOW                                      │
└─────────────────────────────────────────────────────────────────────────┘

1. DISCOVERY FLOW (Scheduled/Manual)
   ┌──────────┐    ┌──────────┐    ┌──────────────────┐    ┌───────────┐
   │ Channels │───▶│ yt-dlp   │───▶│ video_candidates │───▶│ Embeddings│
   │  table   │    │ scanner  │    │     table        │    │ (Python)  │
   └──────────┘    └──────────┘    └──────────────────┘    └───────────┘

2. SELECTION FLOW
   ┌──────────────────┐
   │ video_candidates │
   └────────┬─────────┘
            │
            ▼
   ┌─────────────────┐
   │ Stage 1: Rules  │ (Go)
   └────────┬────────┘
            │ pass
            ▼
   ┌─────────────────┐     ┌──────────────┐
   │ Stage 2: ML     │────▶│ ml-service   │ (Python)
   └────────┬────────┘     └──────────────┘
            │ top_k
            ▼
   ┌─────────────────┐     ┌──────────────┐
   │ Stage 3: LLM    │────▶│   Ollama     │ (Optional)
   └────────┬────────┘     └──────────────┘
            │
            ▼
   ┌─────────────────┐
   │ Selected Videos │
   └─────────────────┘

3. EXECUTION FLOW
   ┌──────────────┐    ┌──────────┐    ┌──────────┐    ┌─────────┐
   │  Selected    │───▶│  yt-dlp  │───▶│  biliup  │───▶│ uploads │
   │   Videos     │    │ download │    │  upload  │    │  table  │
   └──────────────┘    └──────────┘    └──────────┘    └─────────┘

4. FEEDBACK FLOW (Scheduled)
   ┌─────────┐    ┌──────────────┐    ┌───────────────────┐
   │ uploads │───▶│   Bilibili   │───▶│ upload_performance│
   │         │    │   tracker    │    │                   │
   └─────────┘    └──────────────┘    └──────────┬────────┘
                                                  │
                                                  ▼
                  ┌───────────────────┐    ┌─────────────────┐
                  │  upload_outcomes  │───▶│  Retrain Model  │
                  │   (labels)        │    │                 │
                  └───────────────────┘    └─────────────────┘
```

---

## Quick Start

```bash
# 1. Start infrastructure
docker-compose up -d postgres

# 2. Start ML service
cd ml-service
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 3. Start Go backend
cd great-transport-rework-backend
go run ./cmd/yttransfer --http-addr :8080

# 4. (Optional) Start Ollama for LLM review
ollama pull mistral:7b-instruct-q4_K_M
ollama serve

# 5. Add channels and scan
curl -X POST http://localhost:8080/scan \
  -H "Content-Type: application/json" \
  -d '{"channel_id": "UC_xyz", "limit": 10}'

# 6. Run selection pipeline
curl -X POST http://localhost:8080/select \
  -H "Content-Type: application/json" \
  -d '{"limit": 5}'

# 7. Upload selected videos
curl -X POST http://localhost:8080/upload \
  -H "Content-Type: application/json" \
  -d '{"video_ids": ["vid1", "vid2"]}'
```

---

## Success Metrics

| Metric | Description | Target |
|--------|-------------|--------|
| **Precision** | % of uploads achieving >10K views | >60% |
| **Viral Rate** | % of uploads achieving >100K views | >10% |
| **ML Accuracy** | Correlation between predicted and actual score | >0.7 |
| **Throughput** | Videos processed per day | >50 |
| **Automation** | % of decisions requiring no manual review | >90% |

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| ML model drift | Regular retraining, performance monitoring, A/B testing |
| LLM unavailable | Graceful skip, fall back to ML-only selection |
| Bilibili API changes | Abstract tracker interface, monitor for errors |
| Rate limits | Configurable delays, exponential backoff |
| Copyright issues | LLM risk flag detection, manual review queue |
| Cold start (no data) | Start with rule-based only, enable ML after N uploads |
