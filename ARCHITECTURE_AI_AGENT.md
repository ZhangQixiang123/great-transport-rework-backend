# AI-Powered Video Selection System

## Overview

A hybrid decision pipeline that combines rule-based filtering, ML scoring, and local LLM review to intelligently select YouTube videos for cross-platform upload.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              GREAT TRANSPORT v2                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐                │
│  │   YouTube    │     │   Channel    │     │  Scheduler   │                │
│  │   Scanner    │────▶│   Monitor    │────▶│   (Cron)     │                │
│  └──────────────┘     └──────────────┘     └──────────────┘                │
│         │                    │                    │                         │
│         ▼                    ▼                    ▼                         │
│  ┌─────────────────────────────────────────────────────────┐               │
│  │                    VIDEO CANDIDATES                      │               │
│  │                      (Database)                          │               │
│  └─────────────────────────────────────────────────────────┘               │
│                              │                                              │
│                              ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      DECISION PIPELINE                               │   │
│  │  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐                │   │
│  │  │   Stage 1   │   │   Stage 2   │   │   Stage 3   │                │   │
│  │  │   Rules     │──▶│  ML Scorer  │──▶│  LLM Agent  │                │   │
│  │  │  (Filter)   │   │  (Rank)     │   │  (Review)   │                │   │
│  │  └─────────────┘   └─────────────┘   └─────────────┘                │   │
│  │        │                  │                  │                       │   │
│  │        │ reject           │ score            │ approve/reject        │   │
│  │        ▼                  ▼                  ▼                       │   │
│  │  ┌─────────────────────────────────────────────────────────────┐    │   │
│  │  │                   DECISION STORE                             │    │   │
│  │  └─────────────────────────────────────────────────────────────┘    │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                              │                                              │
│                              ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      EXECUTION ENGINE                                │   │
│  │  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐                │   │
│  │  │  Download   │──▶│   Upload    │──▶│  Tracker    │                │   │
│  │  │  (yt-dlp)   │   │  (biliup)   │   │ (metrics)   │                │   │
│  │  └─────────────┘   └─────────────┘   └─────────────┘                │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                              │                                              │
│                              ▼                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    PERFORMANCE MONITOR                               │   │
│  │  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐                │   │
│  │  │  Bilibili   │   │  Analytics  │   │  Feedback   │                │   │
│  │  │  Scraper    │──▶│  Aggregator │──▶│  Loop       │                │   │
│  │  └─────────────┘   └─────────────┘   └─────────────┘                │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘

                              EXTERNAL SERVICES
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                             │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐                │
│  │   Ollama     │     │   YouTube    │     │   Bilibili   │                │
│  │  (Local LLM) │     │   (yt-dlp)   │     │   (biliup)   │                │
│  │              │     │              │     │              │                │
│  │  - Llama 3   │     │  - Metadata  │     │  - Upload    │                │
│  │  - Mistral   │     │  - Download  │     │  - Metrics   │                │
│  │  - Qwen      │     │              │     │              │                │
│  └──────────────┘     └──────────────┘     └──────────────┘                │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Component Details

### 1. Video Discovery Layer

```
┌────────────────────────────────────────────────────────┐
│                  VIDEO DISCOVERY                        │
├────────────────────────────────────────────────────────┤
│                                                        │
│  YouTube Scanner                                       │
│  ├─ Input: Channel IDs (configured watchlist)         │
│  ├─ Method: yt-dlp --flat-playlist                    │
│  ├─ Output: Video IDs + basic metadata                │
│  └─ Frequency: Every 1-6 hours (configurable)         │
│                                                        │
│  Metadata Enricher                                     │
│  ├─ Input: Video IDs from scanner                     │
│  ├─ Method: yt-dlp --dump-json                        │
│  ├─ Output: Full video metadata                       │
│  └─ Includes: views, likes, duration, tags, etc.      │
│                                                        │
└────────────────────────────────────────────────────────┘
```

### 2. Decision Pipeline (Three Stages)

```
┌────────────────────────────────────────────────────────┐
│              STAGE 1: RULE-BASED FILTER                │
├────────────────────────────────────────────────────────┤
│                                                        │
│  Hard Constraints (configurable):                      │
│  ├─ min_views: 1000                                   │
│  ├─ max_age_days: 30                                  │
│  ├─ min_duration_seconds: 60                          │
│  ├─ max_duration_seconds: 3600                        │
│  ├─ blocked_categories: ["News", "Politics"]          │
│  ├─ blocked_keywords: ["sponsor", "ad"]               │
│  └─ required_language: ["en", "zh"]                   │
│                                                        │
│  Output: PASS (to stage 2) or REJECT (with reason)    │
│                                                        │
└────────────────────────────────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────┐
│              STAGE 2: ML SCORING MODEL                 │
├────────────────────────────────────────────────────────┤
│                                                        │
│  Input Features:                                       │
│  ├─ view_count (normalized)                           │
│  ├─ like_ratio (likes / views)                        │
│  ├─ comment_ratio (comments / views)                  │
│  ├─ view_velocity (views / hours_since_upload)        │
│  ├─ channel_avg_views                                 │
│  ├─ channel_subscriber_count                          │
│  ├─ duration_seconds                                  │
│  ├─ category_encoding (one-hot)                       │
│  └─ historical_performance (same channel/category)    │
│                                                        │
│  Model: Gradient Boosting / Random Forest             │
│  Training: Historical upload performance data          │
│  Output: Score 0.0 - 1.0 (predicted success)          │
│                                                        │
│  Threshold: top_k candidates OR score > 0.6           │
│                                                        │
└────────────────────────────────────────────────────────┘
                          │
                          ▼
┌────────────────────────────────────────────────────────┐
│              STAGE 3: LLM AGENT REVIEW                 │
├────────────────────────────────────────────────────────┤
│                                                        │
│  Provider: Ollama (local)                             │
│  Models: llama3:8b, mistral:7b, qwen2:7b              │
│                                                        │
│  Evaluation Criteria:                                  │
│  ├─ Content appropriateness for target platform       │
│  ├─ Title quality and appeal                          │
│  ├─ Potential copyright/legal issues                  │
│  ├─ Cultural fit for Chinese audience                 │
│  └─ Spam/low-quality content detection                │
│                                                        │
│  Input: Title, description, tags, thumbnail URL       │
│  Output: { decision, confidence, reasoning, metadata }│
│                                                        │
│  Metadata suggestions:                                 │
│  ├─ translated_title (Chinese)                        │
│  ├─ suggested_tags                                    │
│  └─ suggested_description                             │
│                                                        │
└────────────────────────────────────────────────────────┘
```

### 3. LLM Agent Detail

```
┌────────────────────────────────────────────────────────┐
│                    LLM AGENT                           │
├────────────────────────────────────────────────────────┤
│                                                        │
│  ┌──────────────────────────────────────────────┐     │
│  │              PROMPT TEMPLATE                  │     │
│  ├──────────────────────────────────────────────┤     │
│  │  System: You are a content curator for a     │     │
│  │  video platform. Evaluate if this YouTube    │     │
│  │  video should be uploaded to Bilibili.       │     │
│  │                                              │     │
│  │  Consider:                                   │     │
│  │  - Content quality and originality           │     │
│  │  - Appeal to Chinese audience                │     │
│  │  - Copyright/legal concerns                  │     │
│  │  - Platform guidelines compliance            │     │
│  │                                              │     │
│  │  Video: {title}                              │     │
│  │  Description: {description}                  │     │
│  │  Tags: {tags}                                │     │
│  │  Duration: {duration}                        │     │
│  │  Views: {views}, Likes: {likes}              │     │
│  │                                              │     │
│  │  Respond in JSON:                            │     │
│  │  {                                           │     │
│  │    "decision": "upload|skip|defer",          │     │
│  │    "confidence": 0.0-1.0,                    │     │
│  │    "reasoning": "...",                       │     │
│  │    "suggested_title_zh": "...",              │     │
│  │    "suggested_tags": ["...", "..."],         │     │
│  │    "risk_flags": ["...", "..."]              │     │
│  │  }                                           │     │
│  └──────────────────────────────────────────────┘     │
│                                                        │
│  Configuration:                                        │
│  ├─ ollama_host: http://localhost:11434              │
│  ├─ model: llama3:8b-instruct-q4_K_M                 │
│  ├─ temperature: 0.3 (low for consistency)           │
│  ├─ timeout: 60s                                     │
│  └─ retry: 3 attempts with backoff                   │
│                                                        │
└────────────────────────────────────────────────────────┘
```

---

## Database Schema

```sql
-- ============================================
-- DISCOVERY & CANDIDATES
-- ============================================

-- Channels to monitor
CREATE TABLE channels (
    channel_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    subscriber_count INTEGER,
    total_views INTEGER,
    video_count INTEGER,
    avg_views_per_video REAL,
    last_scanned_at TIMESTAMP,
    scan_frequency_hours INTEGER DEFAULT 6,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Discovered video candidates
CREATE TABLE video_candidates (
    video_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    title TEXT NOT NULL,
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

    -- Computed fields (updated periodically)
    view_velocity REAL,  -- views per hour since publish
    engagement_rate REAL,  -- (likes + comments) / views

    FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
);

CREATE INDEX idx_candidates_channel ON video_candidates(channel_id);
CREATE INDEX idx_candidates_published ON video_candidates(published_at DESC);
CREATE INDEX idx_candidates_views ON video_candidates(view_count DESC);

-- ============================================
-- DECISION PIPELINE
-- ============================================

-- Rule filter configuration
CREATE TABLE filter_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_name TEXT NOT NULL UNIQUE,
    rule_type TEXT NOT NULL,  -- 'min', 'max', 'blocklist', 'allowlist'
    field TEXT NOT NULL,      -- 'view_count', 'duration_seconds', 'category', etc.
    value TEXT NOT NULL,      -- JSON value
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ML model metadata
CREATE TABLE ml_models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    model_path TEXT NOT NULL,
    feature_columns TEXT NOT NULL,  -- JSON array
    trained_at TIMESTAMP,
    training_samples INTEGER,
    validation_score REAL,
    is_active BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Decision log (all three stages)
CREATE TABLE decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,

    -- Stage 1: Rules
    rule_passed BOOLEAN,
    rule_reject_reason TEXT,

    -- Stage 2: ML
    ml_score REAL,
    ml_model_version TEXT,
    ml_features TEXT,  -- JSON snapshot of input features

    -- Stage 3: LLM
    llm_decision TEXT,  -- 'upload', 'skip', 'defer'
    llm_confidence REAL,
    llm_reasoning TEXT,
    llm_model TEXT,
    llm_suggested_title TEXT,
    llm_suggested_tags TEXT,  -- JSON array
    llm_risk_flags TEXT,      -- JSON array

    -- Final outcome
    final_decision TEXT NOT NULL,  -- 'upload', 'skip', 'defer', 'rule_rejected'
    decided_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (video_id) REFERENCES video_candidates(video_id)
);

CREATE INDEX idx_decisions_video ON decisions(video_id);
CREATE INDEX idx_decisions_final ON decisions(final_decision);
CREATE INDEX idx_decisions_date ON decisions(decided_at DESC);

-- ============================================
-- EXECUTION & TRACKING
-- ============================================

-- Upload records (enhanced from original)
CREATE TABLE uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    decision_id INTEGER,

    -- Upload details
    platform TEXT NOT NULL,  -- 'bilibili', 'tiktok'
    platform_video_id TEXT,  -- ID on target platform
    uploaded_at TIMESTAMP,
    upload_status TEXT,      -- 'pending', 'uploading', 'success', 'failed'
    error_message TEXT,

    -- Metadata used
    title_used TEXT,
    description_used TEXT,
    tags_used TEXT,  -- JSON array

    FOREIGN KEY (video_id) REFERENCES video_candidates(video_id),
    FOREIGN KEY (decision_id) REFERENCES decisions(id)
);

CREATE UNIQUE INDEX idx_uploads_video_platform ON uploads(video_id, platform);

-- Performance tracking (time series)
CREATE TABLE performance_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id INTEGER NOT NULL,
    measured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Metrics from target platform
    views INTEGER,
    likes INTEGER,
    comments INTEGER,
    shares INTEGER,
    favorites INTEGER,

    -- Computed
    views_delta INTEGER,  -- change since last measurement

    FOREIGN KEY (upload_id) REFERENCES uploads(id)
);

CREATE INDEX idx_performance_upload ON performance_metrics(upload_id);
CREATE INDEX idx_performance_time ON performance_metrics(measured_at DESC);

-- ============================================
-- FEEDBACK & LEARNING
-- ============================================

-- Training data for ML model
CREATE TABLE training_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,

    -- Input features (at decision time)
    features TEXT NOT NULL,  -- JSON object

    -- Outcome label (computed from performance)
    outcome_score REAL,  -- 0.0-1.0 based on performance
    outcome_category TEXT,  -- 'high', 'medium', 'low', 'failed'

    -- Metadata
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (video_id) REFERENCES video_candidates(video_id)
);

-- Aggregated statistics for dashboard
CREATE TABLE daily_stats (
    date TEXT PRIMARY KEY,  -- 'YYYY-MM-DD'
    videos_scanned INTEGER DEFAULT 0,
    videos_passed_rules INTEGER DEFAULT 0,
    videos_passed_ml INTEGER DEFAULT 0,
    videos_approved_llm INTEGER DEFAULT 0,
    videos_uploaded INTEGER DEFAULT 0,
    videos_failed INTEGER DEFAULT 0,
    total_views_gained INTEGER DEFAULT 0,
    avg_ml_score REAL,
    avg_llm_confidence REAL
);
```

---

## Data Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           DATA FLOW                                      │
└─────────────────────────────────────────────────────────────────────────┘

1. DISCOVERY FLOW (Scheduled)
   ┌──────────┐    ┌──────────┐    ┌──────────────────┐
   │ Channels │───▶│ yt-dlp   │───▶│ video_candidates │
   │  table   │    │ scanner  │    │     table        │
   └──────────┘    └──────────┘    └──────────────────┘

2. DECISION FLOW (On new candidates)
   ┌──────────────────┐
   │ video_candidates │
   └────────┬─────────┘
            │
            ▼
   ┌─────────────────┐     ┌──────────────┐
   │  Rule Filter    │────▶│ filter_rules │
   └────────┬────────┘     └──────────────┘
            │ pass
            ▼
   ┌─────────────────┐     ┌──────────────┐
   │   ML Scorer     │────▶│  ml_models   │
   └────────┬────────┘     └──────────────┘
            │ top_k
            ▼
   ┌─────────────────┐     ┌──────────────┐
   │   LLM Agent     │────▶│   Ollama     │
   └────────┬────────┘     └──────────────┘
            │
            ▼
   ┌─────────────────┐
   │   decisions     │
   └─────────────────┘

3. EXECUTION FLOW (On approved decisions)
   ┌──────────────┐    ┌──────────┐    ┌──────────┐    ┌─────────┐
   │  decisions   │───▶│  yt-dlp  │───▶│  biliup  │───▶│ uploads │
   │  (approved)  │    │ download │    │  upload  │    │  table  │
   └──────────────┘    └──────────┘    └──────────┘    └─────────┘

4. FEEDBACK FLOW (Scheduled)
   ┌─────────┐    ┌──────────────┐    ┌─────────────────────┐
   │ uploads │───▶│   Bilibili   │───▶│ performance_metrics │
   │         │    │   scraper    │    │                     │
   └─────────┘    └──────────────┘    └──────────┬──────────┘
                                                  │
                                                  ▼
                                      ┌─────────────────────┐
                                      │   training_data     │
                                      │ (for ML retraining) │
                                      └─────────────────────┘
```

---

## Implementation Plan

### Phase 1: Foundation (Week 1-2)

**Goal**: Extend database schema and implement video discovery

```
Tasks:
├─ 1.1 Database Migration
│   ├─ Create migration system (golang-migrate or manual)
│   ├─ Implement new schema tables
│   └─ Migrate existing uploads data
│
├─ 1.2 Video Discovery
│   ├─ Implement channel watchlist management
│   ├─ Create scanner service (yt-dlp --flat-playlist)
│   ├─ Create metadata enricher (yt-dlp --dump-json)
│   └─ Add scheduling (Go ticker or cron)
│
├─ 1.3 Repository Layer
│   ├─ video_candidates CRUD operations
│   ├─ channels CRUD operations
│   └─ Computed field updates (velocity, engagement)
│
└─ 1.4 CLI Extensions
    ├─ Add channel management commands
    ├─ Add candidate listing commands
    └─ Add manual scan trigger
```

**Deliverables**:
- Videos automatically discovered and stored
- Channel management via CLI
- Database with full schema

---

### Phase 2: Rule Engine (Week 3)

**Goal**: Implement configurable rule-based filtering

```
Tasks:
├─ 2.1 Rule Engine Core
│   ├─ Define rule types (min, max, blocklist, allowlist, regex)
│   ├─ Implement rule evaluation engine
│   └─ Support JSON-based rule configuration
│
├─ 2.2 Built-in Rules
│   ├─ View count threshold
│   ├─ Age limit
│   ├─ Duration range
│   ├─ Category blocklist
│   ├─ Keyword blocklist (title/description)
│   └─ Language filter
│
├─ 2.3 Rule Management
│   ├─ CLI commands for rule CRUD
│   ├─ Rule validation
│   └─ Rule priority/ordering
│
└─ 2.4 Decision Logging
    ├─ Log all rule evaluations
    └─ Track rejection reasons
```

**Deliverables**:
- Configurable filtering rules
- Decision audit trail
- Significantly reduced candidate pool

---

### Phase 3: ML Scoring (Week 4-5)

**Goal**: Implement ML-based ranking model

```
Tasks:
├─ 3.1 Feature Engineering
│   ├─ Define feature extraction functions
│   ├─ Implement normalization
│   └─ Handle missing values
│
├─ 3.2 Training Pipeline (Python)
│   ├─ Export training data from SQLite
│   ├─ Train scikit-learn model (Random Forest / XGBoost)
│   ├─ Export to ONNX format
│   └─ Validation and metrics
│
├─ 3.3 Go Inference
│   ├─ Integrate ONNX Runtime (via cgo) OR
│   ├─ Alternative: Call Python subprocess OR
│   ├─ Alternative: Use gorgonia (pure Go)
│   └─ Implement scoring service
│
├─ 3.4 Model Management
│   ├─ Model versioning
│   ├─ A/B testing support
│   └─ Automatic retraining trigger
│
└─ 3.5 Bootstrap Strategy
    ├─ Initial heuristic-based scoring (no ML)
    ├─ Collect performance data
    └─ Train first model after N uploads
```

**Deliverables**:
- Working ML scoring (or heuristic fallback)
- Model training pipeline
- Ranked candidate list

---

### Phase 4: LLM Agent (Week 6-7)

**Goal**: Integrate local LLM for content review

```
Tasks:
├─ 4.1 Ollama Integration
│   ├─ Ollama client library (HTTP API)
│   ├─ Model management (pull, list)
│   ├─ Health checks and fallbacks
│   └─ Configuration (host, model, params)
│
├─ 4.2 Prompt Engineering
│   ├─ Design evaluation prompt
│   ├─ Define JSON output schema
│   ├─ Handle parsing failures
│   └─ Prompt versioning
│
├─ 4.3 Agent Service
│   ├─ Batch processing support
│   ├─ Caching (same video = same result)
│   ├─ Retry with exponential backoff
│   └─ Timeout handling
│
├─ 4.4 Decision Integration
│   ├─ Combine rule + ML + LLM decisions
│   ├─ Confidence thresholds
│   └─ Manual override support
│
└─ 4.5 Metadata Generation
    ├─ Title translation (zh)
    ├─ Tag suggestions
    └─ Description generation
```

**Deliverables**:
- Working LLM review stage
- Auto-generated Chinese metadata
- Complete decision pipeline

---

### Phase 5: Performance Tracking (Week 8)

**Goal**: Track upload performance and create feedback loop

```
Tasks:
├─ 5.1 Bilibili Metrics Scraper
│   ├─ API or web scraping for video stats
│   ├─ Handle authentication
│   └─ Rate limiting
│
├─ 5.2 Metrics Collection
│   ├─ Scheduled polling (every 6 hours)
│   ├─ Delta computation
│   └─ Anomaly detection
│
├─ 5.3 Outcome Labeling
│   ├─ Define success metrics
│   ├─ Compute outcome scores
│   └─ Generate training labels
│
├─ 5.4 Feedback Loop
│   ├─ Auto-generate training data
│   ├─ Trigger model retraining
│   └─ Update rule thresholds
│
└─ 5.5 Analytics Dashboard (Optional)
    ├─ Daily statistics aggregation
    ├─ Prometheus metrics export
    └─ Simple web UI or CLI reports
```

**Deliverables**:
- Performance tracking system
- Self-improving ML model
- Analytics and reporting

---

### Phase 6: Production Hardening (Week 9-10)

**Goal**: Make the system production-ready

```
Tasks:
├─ 6.1 Error Handling
│   ├─ Graceful degradation (LLM down → skip stage)
│   ├─ Comprehensive logging
│   └─ Alerting (optional)
│
├─ 6.2 Configuration
│   ├─ YAML/TOML config file support
│   ├─ Environment variable overrides
│   └─ Secrets management
│
├─ 6.3 Testing
│   ├─ Unit tests for each component
│   ├─ Integration tests
│   └─ Mock external services
│
├─ 6.4 Documentation
│   ├─ Configuration reference
│   ├─ Operational runbook
│   └─ API documentation
│
├─ 6.5 Docker & Deployment
│   ├─ Update Dockerfile
│   ├─ Docker Compose with Ollama
│   └─ Volume management
│
└─ 6.6 Monitoring
    ├─ Health endpoints
    ├─ Metrics (decisions/day, success rate)
    └─ Log aggregation
```

**Deliverables**:
- Production-ready system
- Comprehensive documentation
- Monitoring and alerting

---

## Technology Choices

### Local LLM Options (via Ollama)

| Model | Size | Speed | Quality | Recommendation |
|-------|------|-------|---------|----------------|
| llama3:8b | 4.7GB | Fast | Good | **Default choice** |
| llama3:70b | 40GB | Slow | Excellent | If GPU available |
| mistral:7b | 4.1GB | Fast | Good | Alternative |
| qwen2:7b | 4.4GB | Fast | Good | Better for Chinese |
| phi3:mini | 2.3GB | Very Fast | Moderate | Resource-constrained |

**Recommendation**: Start with `llama3:8b-instruct-q4_K_M` (quantized for speed), upgrade to `qwen2:7b` for better Chinese output.

### ML Model Options

| Approach | Pros | Cons |
|----------|------|------|
| ONNX Runtime (cgo) | Fast inference, production-ready | Complex build, cgo dependency |
| Python subprocess | Easy training, scikit-learn | Process overhead, Python dependency |
| gorgonia (pure Go) | No external deps | Limited model types, less mature |
| Pre-computed scores | Simple, no ML infra | Manual retraining |

**Recommendation**: Start with Python subprocess for flexibility, migrate to ONNX Runtime for production.

---

## Directory Structure (Proposed)

```
great-transport-rework-backend/
├── cmd/
│   └── yttransfer/
│       └── main.go
├── internal/
│   ├── app/
│   │   ├── controller.go      # Enhanced orchestration
│   │   ├── downloader.go
│   │   ├── uploader_biliup.go
│   │   ├── http.go
│   │   └── store.go           # Legacy (migrate to repository)
│   ├── discovery/
│   │   ├── scanner.go         # Channel scanning
│   │   └── enricher.go        # Metadata enrichment
│   ├── pipeline/
│   │   ├── pipeline.go        # Decision pipeline orchestrator
│   │   ├── rules.go           # Rule engine
│   │   ├── scorer.go          # ML scoring
│   │   └── agent.go           # LLM agent
│   ├── repository/
│   │   ├── channels.go
│   │   ├── candidates.go
│   │   ├── decisions.go
│   │   ├── uploads.go
│   │   └── metrics.go
│   ├── llm/
│   │   ├── client.go          # Ollama HTTP client
│   │   ├── prompt.go          # Prompt templates
│   │   └── parser.go          # Response parsing
│   ├── ml/
│   │   ├── features.go        # Feature extraction
│   │   ├── inference.go       # Model inference
│   │   └── training/          # Python training scripts
│   └── metrics/
│       ├── collector.go       # Bilibili stats collector
│       └── feedback.go        # Training data generation
├── migrations/
│   ├── 001_initial.sql
│   └── 002_ai_agent.sql
├── configs/
│   └── config.example.yaml
├── scripts/
│   ├── docker-biliup-login.sh
│   └── train_model.py
├── Dockerfile
├── docker-compose.yml         # With Ollama service
├── go.mod
├── README.md
├── DEVELOPER.md
└── ARCHITECTURE_AI_AGENT.md   # This file
```

---

## Quick Start (After Implementation)

```bash
# 1. Start Ollama
ollama pull llama3:8b-instruct-q4_K_M
ollama serve

# 2. Initialize database
./yt-transfer migrate up

# 3. Add channels to watch
./yt-transfer channel add UC_xyz --name "Example Channel"

# 4. Configure rules
./yt-transfer rule set min_views 5000
./yt-transfer rule set max_age_days 14

# 5. Run discovery + decision pipeline
./yt-transfer scan --evaluate

# 6. Review decisions
./yt-transfer decisions list --pending

# 7. Execute approved uploads
./yt-transfer upload --approved

# 8. Or run everything automatically
./yt-transfer daemon --scan-interval 6h --upload-interval 1h
```

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| LLM hallucination | Low temperature, structured JSON output, validation |
| LLM unavailable | Graceful skip, fall back to ML-only |
| ML model drift | Regular retraining, performance monitoring |
| Rate limits | Configurable delays, exponential backoff |
| Storage growth | Retention policies, archival strategy |
| Copyright issues | LLM risk flag detection, manual review queue |

---

## Success Metrics

1. **Precision**: % of uploaded videos that achieve >1000 views
2. **Recall**: % of high-potential videos correctly identified
3. **Efficiency**: Videos processed per hour
4. **Cost**: LLM inference time/cost per decision
5. **Automation**: % of decisions requiring no manual intervention
