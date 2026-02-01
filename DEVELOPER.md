# Developer Manual

This document explains the Great Transport system architecture, how to run it locally, and where to make changes.

## System Overview

Great Transport is a **hybrid Go + Python system** for intelligent video selection and cross-platform upload.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           SYSTEM ARCHITECTURE                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                        GO BACKEND                                    │   │
│  │  CLI/HTTP → Controller → Downloader ──yt-dlp──→ files → Uploader   │   │
│  │                    ↘ Store (tracks state)                           │   │
│  │                    ↘ Rule Engine (filtering)                        │   │
│  │                    ↘ ML Client (→ Python service)                   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                      │                                      │
│                                      ▼ HTTP/gRPC                           │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                      PYTHON ML SERVICE                               │   │
│  │  FastAPI → Embedding Service (CLIP, BERT)                           │   │
│  │         → Prediction Service (LightGBM)                             │   │
│  │         → Training Pipeline                                          │   │
│  │         → Bilibili Tracker                                           │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                      │                                      │
│                                      ▼                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    POSTGRESQL + PGVECTOR                             │   │
│  │  Shared database for both services                                   │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Language | Responsibility |
|-----------|----------|----------------|
| **Go Backend** | Go | CLI, HTTP API, orchestration, yt-dlp, biliup |
| **ML Service** | Python | Embeddings, ML inference, training, Bilibili tracking |
| **Database** | PostgreSQL | Shared state, vector storage (pgvector) |
| **Ollama** | Go | Local LLM inference (optional) |

---

## Directory Layout

```
great-transport/
├── great-transport-rework-backend/     # Go Backend
│   ├── cmd/yttransfer/
│   │   └── main.go                     # Entry point, flag parsing
│   ├── internal/app/
│   │   ├── controller.go               # Sync orchestration
│   │   ├── downloader.go               # yt-dlp wrapper
│   │   ├── uploader_biliup.go          # biliup wrapper
│   │   ├── store.go                    # Database operations
│   │   ├── repository.go               # Data models
│   │   ├── rules.go                    # Rule engine
│   │   ├── scanner.go                  # YouTube scanner
│   │   ├── http.go                     # HTTP server
│   │   ├── ml_client.go                # ML service client (future)
│   │   └── selector.go                 # Selection pipeline (future)
│   ├── downloads/                      # Default output directory
│   └── metadata.db                     # SQLite database (dev only)
│
├── ml-service/                         # Python ML Service
│   ├── app/
│   │   ├── main.py                     # FastAPI app
│   │   ├── api/                        # API endpoints
│   │   ├── services/                   # Business logic
│   │   ├── models/                     # ML model wrappers
│   │   └── collectors/                 # Data collectors
│   ├── training/                       # Training scripts
│   └── notebooks/                      # Jupyter notebooks
│
└── docker-compose.yml                  # Full stack deployment
```

---

## Local Development Setup

### Prerequisites

1. **Go 1.22+**
2. **Python 3.11+**
3. **PostgreSQL 16** with pgvector extension (or SQLite for dev)
4. **yt-dlp**: `brew install yt-dlp` (macOS) or `pip install yt-dlp`
5. **ffmpeg** (optional, for merged mp4 outputs)
6. **biliup**: `pip install biliup` (for Bilibili uploads)

### Go Backend Setup

```bash
cd great-transport-rework-backend

# Install dependencies
go mod download

# Run tests
go test ./...

# Run locally (CLI mode)
go run ./cmd/yttransfer --help

# Run locally (HTTP mode)
go run ./cmd/yttransfer --http-addr :8080
```

### Python ML Service Setup

```bash
cd ml-service

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt

# Run locally
uvicorn app.main:app --reload --port 8000

# Run tests
pytest
```

### Database Setup

#### Option A: SQLite (Development)
```bash
# SQLite database is auto-created at --db-path (default: metadata.db)
go run ./cmd/yttransfer --list-channels
```

#### Option B: PostgreSQL (Production)
```bash
# Start PostgreSQL with pgvector
docker run -d \
  --name postgres \
  -e POSTGRES_USER=transport \
  -e POSTGRES_PASSWORD=transport \
  -e POSTGRES_DB=great_transport \
  -p 5432:5432 \
  pgvector/pgvector:pg16

# Set DATABASE_URL environment variable
export DATABASE_URL="postgres://transport:transport@localhost:5432/great_transport"
```

### Docker Compose (Full Stack)

```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop all services
docker-compose down
```

---

## Go Backend Details

### CLI Usage

```bash
# Single video download + upload
go run ./cmd/yttransfer --video-id dQw4w9WgXcQ --platform bilibili

# Channel sync (batch)
go run ./cmd/yttransfer --channel-id UC_x5XG1OV2P6uZZ5FSM9Ttw --limit 3

# Channel management
go run ./cmd/yttransfer --add-channel "https://www.youtube.com/@Channel"
go run ./cmd/yttransfer --list-channels
go run ./cmd/yttransfer --scan-channel UC_xxxxx --limit 10

# Rule management
go run ./cmd/yttransfer --list-rules
go run ./cmd/yttransfer --set-rule "min_views=5000"
go run ./cmd/yttransfer --filter --limit 100

# HTTP server mode
go run ./cmd/yttransfer --http-addr :8080
```

### Key CLI Flags

| Flag | Description | Default |
|------|-------------|---------|
| `--channel-id` | YouTube channel ID or URL | - |
| `--video-id` | Single YouTube video ID | - |
| `--platform` | Target platform (bilibili, tiktok) | bilibili |
| `--output` | Download directory | downloads |
| `--db-path` | SQLite database path | metadata.db |
| `--limit` | Max videos to process | 5 |
| `--sleep-seconds` | Delay between downloads | 0 |
| `--http-addr` | HTTP server address | - (disabled) |

### HTTP API

```bash
# Start HTTP server
go run ./cmd/yttransfer --http-addr :8080

# Trigger channel sync
curl -X POST http://localhost:8080/sync \
  -H "Content-Type: application/json" \
  -d '{"channel_id":"UC123","limit":3}'

# Response
{
  "considered": 10,
  "skipped": 7,
  "downloaded": 3,
  "uploaded": 3
}
```

### Code Organization

| File | Purpose |
|------|---------|
| `main.go` | Flag parsing, dependency injection, mode selection |
| `controller.go` | Orchestrates scan → download → upload flow |
| `downloader.go` | yt-dlp wrapper (list videos, download, metadata) |
| `uploader_biliup.go` | biliup wrapper for Bilibili uploads |
| `store.go` | SQLite/PostgreSQL persistence |
| `repository.go` | Data model structs |
| `rules.go` | Rule engine (min, max, blocklist, allowlist, regex, age_days) |
| `scanner.go` | Channel scanning service |
| `http.go` | HTTP server and handlers |

---

## Python ML Service Details

### API Endpoints

```
GET  /health                        → Health check
POST /api/v1/embeddings/generate    → Generate embeddings for video
POST /api/v1/predictions/score      → Get ML prediction for video
POST /api/v1/predictions/batch      → Batch predictions
POST /api/v1/training/trigger       → Trigger model retraining
GET  /api/v1/models/active          → Get active model info
```

### Code Organization

| Directory | Purpose |
|-----------|---------|
| `app/api/` | FastAPI route handlers |
| `app/services/` | Business logic (embeddings, features, predictions) |
| `app/models/` | ML model wrappers (LightGBM, CLIP, BERT) |
| `app/collectors/` | Data collection (Bilibili tracker, competitor monitor) |
| `training/` | Training scripts for offline model training |
| `notebooks/` | Jupyter notebooks for exploration |

### Key Services

#### EmbeddingService
```python
class EmbeddingService:
    def embed_text(self, text: str) -> list[float]:
        """Generate DistilBERT embedding (768d)"""

    def embed_thumbnail(self, url: str) -> list[float]:
        """Generate CLIP embedding (512d)"""
```

#### PredictionService
```python
class PredictionService:
    def predict(self, video_id: str) -> dict:
        """Return score (0-1) and label (viral/successful/standard/failed)"""
```

#### BilibiliTracker
```python
class BilibiliTracker:
    async def get_video_stats(self, bvid: str) -> dict:
        """Fetch video metrics from Bilibili"""

    async def track_all_uploads(self, db_session):
        """Update performance metrics for all our uploads"""
```

---

## Database Schema

### Core Tables (Go Backend)

```sql
-- channels: YouTube channels to monitor
-- video_candidates: Discovered videos with metadata
-- filter_rules: Configurable filtering rules
-- rule_decisions: Rule evaluation audit log
-- uploads: Upload records with status
```

### ML Tables (Python Service)

```sql
-- upload_performance: Time-series metrics (1h, 24h, 7d, 30d)
-- upload_outcomes: Final success labels
-- competitor_channels: Channels to learn from
-- competitor_videos: Training data from competitors
-- ml_predictions: Prediction audit log
-- feature_store: Precomputed features
-- model_registry: Model versioning
```

### Vector Storage (pgvector)

```sql
-- video_candidates.title_embedding vector(768)
-- video_candidates.thumbnail_embedding vector(512)
-- video_candidates.description_embedding vector(768)
```

---

## Development Workflow

### Adding a New Feature

1. **Database Changes**
   - Add migration in `migrations/` (or modify `store.go` for SQLite)
   - Update `repository.go` with new structs

2. **Go Backend Changes**
   - Implement business logic in `internal/app/`
   - Add CLI flags in `main.go`
   - Add HTTP handlers in `http.go`
   - Write tests in `*_test.go`

3. **Python ML Changes**
   - Add service in `ml-service/app/services/`
   - Add API endpoint in `ml-service/app/api/`
   - Write tests in `ml-service/tests/`

### Testing

```bash
# Go tests
cd great-transport-rework-backend
go test ./... -v

# Python tests
cd ml-service
pytest -v

# Integration tests (requires Docker)
docker-compose -f docker-compose.test.yml up --abort-on-container-exit
```

### Code Style

- **Go**: Follow standard Go conventions, use `gofmt`
- **Python**: Follow PEP 8, use `black` and `isort`

```bash
# Go formatting
gofmt -w .

# Python formatting
black ml-service/
isort ml-service/
```

---

## Common Tasks

### Clear Database State
```bash
# SQLite
rm metadata.db

# PostgreSQL
docker-compose exec postgres psql -U transport -d great_transport -c "TRUNCATE uploads, video_candidates CASCADE;"
```

### Retrain ML Model
```bash
cd ml-service
python training/train_ranker.py --min-samples 1000

# Or via API
curl -X POST http://localhost:8000/api/v1/training/trigger \
  -H "Content-Type: application/json" \
  -d '{"model_name": "ranker", "min_samples": 1000}'
```

### Update Bilibili Cookies
```bash
# biliup login generates cookies.json
biliup login

# Copy to expected location
cp ~/.biliup/cookies.json ./cookies.json
```

### Monitor Logs
```bash
# Go backend
go run ./cmd/yttransfer --http-addr :8080 2>&1 | tee app.log

# Python service
uvicorn app.main:app --log-level debug

# Docker Compose
docker-compose logs -f go-backend ml-service
```

---

## Troubleshooting

### yt-dlp Issues

**SABR/DASH failures**:
The downloader automatically retries with `--allow-dynamic-mpd --concurrent-fragments 1`.

**403 Forbidden**:
Usually rate limiting. Increase `--sleep-seconds` or use cookies.

### Bilibili Upload Issues

**Login expired**:
Run `biliup login` to refresh cookies.

**Upload failed**:
Check `cookies.json` exists and is valid.

### ML Service Issues

**CUDA out of memory**:
Reduce batch size or use CPU inference.

**Model not found**:
Check `model_registry` table for active model.

---

## Architecture Documents

- [ARCHITECTURE_AI_AGENT.md](./ARCHITECTURE_AI_AGENT.md) - Full system architecture
- [PHASES.md](./PHASES.md) - Implementation phases and roadmap
- [TEST_COVERAGE.md](./TEST_COVERAGE.md) - Test documentation

---

## External Dependencies

### Go
- `modernc.org/sqlite` - Pure Go SQLite driver
- `github.com/lib/pq` or `github.com/jackc/pgx` - PostgreSQL driver

### Python
- `fastapi` - Web framework
- `lightgbm` - ML model
- `transformers` - CLIP + BERT embeddings
- `bilibili-api-python` - Bilibili API client
- `sqlalchemy` - Database ORM
- `asyncpg` - Async PostgreSQL driver

### External Tools
- `yt-dlp` - YouTube download
- `ffmpeg` - Video processing
- `biliup` - Bilibili upload
- `ollama` - Local LLM (optional)
