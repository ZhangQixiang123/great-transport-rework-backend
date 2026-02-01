# Great Transport - Implementation Phases

## Overview

This document outlines the phased implementation plan for the Great Transport ML-powered video selection system.

---

## Phase 1: Foundation (Completed)

**Goal**: Automatically discover and store video candidates with full metadata

### Deliverables
- YouTube channel scanner using yt-dlp
- Video candidate storage with computed metrics (view_velocity, engagement_rate)
- Channel management CLI commands
- SQLite database with core schema

### CLI Commands
| Command | Description |
|---------|-------------|
| `--add-channel URL` | Add a channel to the watchlist |
| `--remove-channel ID` | Remove a channel |
| `--list-channels` | List all watched channels |
| `--scan` | Scan all active channels |
| `--scan-channel ID` | Scan a specific channel |
| `--list-candidates` | List discovered candidates |

### Verification
```bash
./yt-transfer --add-channel "https://www.youtube.com/@SomeChannel"
./yt-transfer --scan-channel UC_xxxxx --limit 10
./yt-transfer --list-candidates --candidate-limit 20
```

---

## Phase 2: Rule Engine (Completed)

**Goal**: Implement configurable rule-based filtering (Stage 1 of selection pipeline)

### Deliverables
- Rule engine with 6 rule types (min, max, blocklist, allowlist, regex, age_days)
- Priority-based rule evaluation
- Decision logging with rejection reasons
- Default rules for common filtering

### Rule Types
| Type | Description | Example |
|------|-------------|---------|
| `min` | Minimum threshold | min_views = 1000 |
| `max` | Maximum threshold | max_duration = 3600 |
| `blocklist` | Reject if in list | blocked_categories = ["News"] |
| `allowlist` | Accept only if in list | allowed_languages = ["en", "zh"] |
| `regex` | Reject if pattern matches | title_blocklist = "(?i)sponsor" |
| `age_days` | Max age since publish | max_age = 30 |

### CLI Commands
| Command | Description |
|---------|-------------|
| `--list-rules` | List all filter rules |
| `--set-rule NAME=VALUE` | Set/update a rule value |
| `--add-rule JSON` | Add a custom rule |
| `--remove-rule NAME` | Remove a rule |
| `--filter` | Run rule filter on candidates |
| `--list-filtered` | List passed candidates |
| `--list-rejected` | List rejected candidates |

### Verification
```bash
./yt-transfer --list-rules
./yt-transfer --set-rule "min_views=5000"
./yt-transfer --filter --limit 100
./yt-transfer --list-filtered
```

---

## Phase 3A: Statistics System (Planned)

**Goal**: Track uploaded video performance on Bilibili

### Components

#### 1. Enhanced Upload Tracking
- Store Bilibili video ID (bvid) after upload
- Record source metrics at upload time
- Link uploads to predictions

#### 2. Performance Tracker (Python)
```python
# Collect metrics at scheduled intervals
checkpoints = [1, 6, 24, 48, 168, 720]  # hours

metrics = {
    "views", "likes", "coins", "favorites",
    "shares", "danmaku", "comments"
}
```

#### 3. Database Tables
```sql
-- upload_performance: Time-series metrics
-- upload_outcomes: Final labels for training
```

### Deliverables
- Bilibili API integration (bilibili-api-python)
- Scheduled performance collection
- Performance dashboard queries

### CLI Commands
| Command | Description |
|---------|-------------|
| `--track-performance` | Collect metrics for recent uploads |
| `--stats` | Show upload statistics |
| `--stats-detail VIDEO_ID` | Show detailed performance |

---

## Phase 3B: Data Collection (Planned)

**Goal**: Collect training data from competitor channels

### Components

#### 1. Competitor Monitor
- Identify successful transporter channels on Bilibili
- Track their video selection patterns
- Collect performance metrics

#### 2. Training Data Pipeline
- Map competitor videos to YouTube sources (when identifiable)
- Label success based on performance thresholds
- Store in competitor_videos table

#### 3. Success Labeling
| Label | Bilibili Criteria |
|-------|-------------------|
| viral | >1M views, >5% engagement, >10K coins |
| successful | >100K views, >3% engagement |
| standard | >10K views, 1-3% engagement |
| failed | <10K views or <1% engagement |

### Deliverables
- Competitor channel discovery
- Automated data collection
- Labeled training dataset

---

## Phase 3C: Feature Engineering (Planned)

**Goal**: Build comprehensive feature set for ML model

### Feature Categories

#### Content Features
- duration_bucket (short/medium/long)
- title_length, has_question_mark, has_number
- category_encoded, language_encoded

#### Engagement Features (from source)
- view_velocity (views per hour)
- like_ratio, comment_ratio
- engagement_rate

#### Time Features
- upload_hour, upload_day_of_week
- days_since_publish

#### Embedding Features
- title_embedding (DistilBERT, 768d)
- thumbnail_embedding (CLIP, 512d)
- description_embedding (768d)

#### Similarity Features
- similar_video_avg_performance (via embedding similarity)
- category_avg_performance

#### Channel Features
- channel_avg_engagement
- channel_upload_frequency

### Components

#### 1. Embedding Service (Python)
```python
class EmbeddingService:
    def embed_text(self, text: str) -> list[float]:
        # DistilBERT embeddings

    def embed_thumbnail(self, url: str) -> list[float]:
        # CLIP embeddings
```

#### 2. Feature Store
- Precomputed features for fast inference
- Automatic feature refresh

### Deliverables
- CLIP + DistilBERT embedding generation
- Feature store with 50+ features
- Vector similarity search (pgvector)

---

## Phase 3D: ML Training (Planned)

**Goal**: Train ranking model on historical data

### Components

#### 1. Training Pipeline
```python
# training/train_ranker.py
def train_model(df: pd.DataFrame) -> lgb.Booster:
    # Feature preparation
    # Train/test split
    # LightGBM training
    # Evaluation
    # Save to model registry
```

#### 2. Model Options
| Model | Pros | Cons |
|-------|------|------|
| LightGBM | Fast training, good with tabular | Less interpretable |
| XGBoost | Robust, well-documented | Slower than LightGBM |
| Random Forest | Simple, interpretable | May underperform |

#### 3. Evaluation Metrics
- RMSE (regression on success score)
- AUC-ROC (classification)
- Precision@K (ranking quality)
- Correlation with actual performance

### Deliverables
- Training script with hyperparameter tuning
- Model evaluation framework
- Model registry with versioning

---

## Phase 3E: Integration (Planned)

**Goal**: Connect Go backend to Python ML service

### Components

#### 1. ML Client (Go)
```go
type MLClient struct {
    baseURL string
    client  *http.Client
}

func (c *MLClient) Predict(ctx context.Context, videoID string) (*Prediction, error)
func (c *MLClient) GenerateEmbeddings(ctx context.Context, video VideoCandidate) error
```

#### 2. Selection Pipeline
```go
func (s *Selector) SelectVideos(ctx context.Context, limit int) ([]VideoCandidate, error) {
    // Stage 1: Get rule-filtered candidates
    // Stage 2: Score with ML model
    // Stage 3: (Optional) LLM review
    // Return top candidates
}
```

#### 3. Python ML Service (FastAPI)
```
POST /api/v1/embeddings/generate
POST /api/v1/predictions/score
POST /api/v1/predictions/batch
GET  /health
```

### Deliverables
- gRPC or REST client in Go
- FastAPI ML service
- End-to-end selection pipeline

---

## Phase 4: Feedback Loop (Planned)

**Goal**: Continuous learning from upload performance

### Components

#### 1. Outcome Labeling
- Automatic labeling after 30 days (or when metrics stabilize)
- Configurable success thresholds

#### 2. Training Data Generation
- Link features at prediction time to actual outcomes
- Maintain training dataset versioning

#### 3. Scheduled Retraining
- Trigger retraining when new labeled data exceeds threshold
- A/B test new model vs current
- Automatic model promotion if improved

#### 4. Model Monitoring
- Track prediction vs actual correlation
- Alert on model drift
- Dashboard with model performance

### Deliverables
- Auto-labeling pipeline
- Scheduled retraining jobs
- A/B testing framework
- Model monitoring dashboard

---

## Phase 5: LLM Integration (Optional)

**Goal**: Add LLM-based content review as final selection stage

### Components

#### 1. Ollama Integration
```go
type OllamaClient struct {
    host  string
    model string
}

func (c *OllamaClient) Review(ctx context.Context, video VideoCandidate) (*LLMDecision, error)
```

#### 2. Prompt Engineering
- Evaluate content appropriateness
- Detect copyright/legal risks
- Suggest Chinese title translation
- Identify spam/low-quality content

#### 3. Configuration
| Setting | Default | Description |
|---------|---------|-------------|
| model | mistral:7b-instruct-q4_K_M | Ollama model |
| temperature | 0.3 | Low for consistency |
| timeout | 60s | Request timeout |
| enabled | false | Enable/disable stage |

### Deliverables
- Ollama client library
- Configurable prompt templates
- LLM decision logging

---

## Phase 6: Production Hardening (Planned)

**Goal**: Production-ready deployment

### Components

#### 1. Database Migration
- SQLite → PostgreSQL migration
- Enable pgvector extension
- Create indexes for performance

#### 2. Docker Deployment
```yaml
services:
  - postgres (pgvector/pgvector:pg16)
  - go-backend
  - ml-service
  - ollama (optional)
```

#### 3. Monitoring
- Prometheus metrics export
- Grafana dashboards
- Health endpoints

#### 4. Error Handling
- Graceful degradation (ML service down → rule-only)
- Retry with exponential backoff
- Comprehensive logging

### Deliverables
- Docker Compose configuration
- Database migration scripts
- Monitoring setup
- Operational documentation

---

## Phase Summary

| Phase | Status | Description |
|-------|--------|-------------|
| 1 | ✅ Completed | Foundation - Video discovery |
| 2 | ✅ Completed | Rule Engine - Filtering |
| 3A | 📋 Planned | Statistics System |
| 3B | 📋 Planned | Data Collection |
| 3C | 📋 Planned | Feature Engineering |
| 3D | 📋 Planned | ML Training |
| 3E | 📋 Planned | Integration |
| 4 | 📋 Planned | Feedback Loop |
| 5 | 📋 Planned | LLM Integration (Optional) |
| 6 | 📋 Planned | Production Hardening |

---

## Dependencies Between Phases

```
Phase 1 (Foundation)
    │
    ▼
Phase 2 (Rule Engine)
    │
    ├──────────────────────┐
    ▼                      ▼
Phase 3A (Statistics)   Phase 3C (Features)
    │                      │
    ▼                      ▼
Phase 3B (Data)         Phase 3D (ML Training)
    │                      │
    └──────────┬───────────┘
               ▼
         Phase 3E (Integration)
               │
               ▼
         Phase 4 (Feedback Loop)
               │
    ┌──────────┴──────────┐
    ▼                     ▼
Phase 5 (LLM)      Phase 6 (Production)
(Optional)
```

---

## Quick Reference: File Changes by Phase

### Phase 3A: Statistics System
| File | Action | Description |
|------|--------|-------------|
| `ml-service/app/collectors/bilibili_tracker.py` | Create | Bilibili API integration |
| `internal/app/store.go` | Modify | Add performance tables |
| `internal/app/stats.go` | Create | Statistics queries |

### Phase 3B: Data Collection
| File | Action | Description |
|------|--------|-------------|
| `ml-service/app/collectors/competitor_monitor.py` | Create | Competitor tracking |
| `ml-service/app/collectors/labeler.py` | Create | Auto-labeling |

### Phase 3C: Feature Engineering
| File | Action | Description |
|------|--------|-------------|
| `ml-service/app/services/embedding_service.py` | Create | CLIP + BERT |
| `ml-service/app/services/feature_service.py` | Create | Feature extraction |
| `ml-service/app/api/embeddings.py` | Create | Embedding API |

### Phase 3D: ML Training
| File | Action | Description |
|------|--------|-------------|
| `ml-service/training/train_ranker.py` | Create | Training script |
| `ml-service/app/models/ranker.py` | Create | Model wrapper |

### Phase 3E: Integration
| File | Action | Description |
|------|--------|-------------|
| `internal/app/ml_client.go` | Create | ML service client |
| `internal/app/selector.go` | Create | Selection pipeline |
| `ml-service/app/api/predictions.py` | Create | Prediction API |
