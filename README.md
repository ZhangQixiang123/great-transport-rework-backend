# Great Transport — YouTube-to-Bilibili Video Transport System

Automated pipeline that discovers promising YouTube videos, scores them for transportability, translates titles to Chinese, generates Bilibili descriptions, and uploads them via a Go download/upload service.

## Architecture

```
Python (ml-service/)                          Go (internal/app/)
┌─────────────────────────────┐              ┌──────────────────────┐
│  real_run.py                │              │  HTTP server (:8081) │
│                             │              │                      │
│  Phase A: Strategy Gen (LLM)│              │  POST /upload        │
│  Phase B: Market Analysis   │   HTTP POST  │    ├─ yt-dlp download│
│  Phase C: YouTube Search    │  ──────────► │    ├─ biliup upload  │
│  Phase D: Yield Reflection  │              │    └─ return bvid    │
│  Phase E: Upload            │              │                      │
│    ├─ Translate title (LLM) │              └──────────────────────┘
│    ├─ Generate desc (LLM)   │
│    └─ POST to Go service    │
└─────────────────────────────┘
```

**Python** handles all intelligence: LLM-powered strategy generation, Bilibili market analysis, YouTube search, heuristic scoring, transportability checks, title translation, and description generation.

**Go** handles all media: downloading videos from YouTube (yt-dlp) and uploading to Bilibili (biliup). It exposes a single `POST /upload` endpoint and processes jobs synchronously.

## Requirements

- Python 3.12+ (venv in `ml-service/.venv`)
- Go 1.22+
- `yt-dlp` in PATH
- `ffmpeg` in PATH
- [`biliup`](https://github.com/biliup/biliup) — `ml-service/.venv/Scripts/biliup.exe`
- Bilibili cookies: `scripts/cookies.json` (run `biliup --user-cookie cookies.json login` once)
- YouTube Data API key (configured in `enrich_youtube.py`)
- LLM backend: Ollama (local), OpenAI, or Anthropic

## Quick Start

### Dry run (preview titles + descriptions, no upload)

```bash
cd ml-service
.venv\Scripts\python real_run.py --backend ollama --dry-run
```

This runs the full pipeline and prints the exact JSON payload that would be sent to the Go service for each selected video, including the LLM-translated Chinese title and generated description. No Go server needed.

### Real upload run

```bash
# Terminal 1: Start Go upload service
go run ./cmd/yttransfer --http-addr :8081 --platform bilibili \
  --biliup-cookie scripts/cookies.json \
  --biliup-binary ml-service/.venv/Scripts/biliup.exe

# Terminal 2: Run discovery + upload pipeline
cd ml-service
.venv\Scripts\python real_run.py --backend ollama --upload --go-url http://localhost:8081
```

### CLI options (real_run.py)

| Flag | Default | Description |
|------|---------|-------------|
| `--backend` | `ollama` | LLM backend: `ollama`, `openai`, `anthropic` |
| `--model` | per-backend | LLM model name |
| `--db-path` | `data.db` | SQLite database path |
| `--max-queries` | `5` | Max search queries from strategy generation |
| `--max-age-days` | `60` | YouTube recency filter |
| `--top-n` | `3` | Top candidates for transportability check + upload |
| `--skip-reflection` | off | Skip Loop 1 yield reflection (saves LLM calls) |
| `--upload` | off | Enable upload (requires running Go server) |
| `--dry-run` | off | Generate titles + descriptions, print payloads, skip upload |
| `--go-url` | `http://localhost:8080` | Go upload service URL |

## Pipeline Phases

### Phase A: Strategy Generation (1 LLM call)
Generates YouTube search queries from active strategies and Bilibili trending keywords. Uses `StrategyGenerationSkill` with self-improving principles.

### Phase B: Market Analysis (1 LLM call per query)
Searches Bilibili for each query to check saturation. `MarketAnalysisSkill` assesses opportunity score, quality gaps, and freshness gaps. Saturated queries are filtered out.

### Phase C: YouTube Search + Scoring
Searches YouTube via Data API, then scores candidates with a heuristic formula:
```
score = (engagement * w1 + view_signal * w2 + opportunity * w3 + duration * w4) * category_bonus
```
Scoring parameters are bootstrapped from historical data.

### Phase D: Yield Reflection (Loop 1)
LLM reflects on query yield rates and strategy performance. May update YouTube/Bilibili principles and suggest new channels to follow. Skippable with `--skip-reflection`.

### Phase E: Upload
For each top candidate that passes transportability check:
1. **Translate title** — LLM translates English title to Chinese
2. **Generate description** — LLM writes a fun Chinese intro sentence + template with YouTube view count and link
3. **Print payload** — Shows exact JSON that will be sent to Go service
4. **Submit upload** — POSTs to Go service which downloads via yt-dlp and uploads via biliup (skipped in `--dry-run`)

## ML Components

### Prediction Fallback Chain
```
Neural Reranker → LLM Predictor → LightGBM
```

### LightGBM (fallback)
- Regression on log(views) with 48 features
- 10 pre-upload + 3 clickbait + 7 YouTube + 3 additional + 20 title embeddings + 5 RAG
- Two modes: GPBoost (random intercepts) / pure LightGBM

### Neural Reranker (PyTorch)
- Cross-encoder with multi-head attention over similar videos
- 15 candidate numeric + 8 LLM + 2 categorical features + up to 20 similar videos
- Training: GroupKFold by channel, AdamW + OneCycleLR, mixed precision

### Embeddings & RAG
- TitleEmbedder: frozen MiniLM + projection (384d → 128d), trained with MSE on log(views)
- VectorStore: numpy cosine similarity, top-20 similar videos, 5 RAG features

### Skill Framework
- `app/skills/` — Self-improving skills with version tracking
- `app/scoring/` — Heuristic scoring with bootstrapped parameters
- `app/search/` — Aggregator combining YouTube + Bilibili search results
- `app/outcomes/` — Outcome tracking for feedback loops

## Data

- SQLite database: `ml-service/data.db`
- 7,791 videos from 31 channels
- 5,439 with YouTube source IDs, 4,493 with YouTube stats
- Tables: `competitor_channels`, `competitor_videos`, `youtube_stats`, `skills`, `skill_versions`, `strategies`, `strategy_runs`, `followed_channels`, `scoring_params`, `upload_jobs`

## Testing

```bash
cd ml-service
.venv\Scripts\python -m pytest tests/ -v
```

607 tests total (605 pass, 2 pre-existing failures unrelated to core functionality).

## Project Structure

```
├── cmd/yttransfer/          # Go CLI entrypoint
├── internal/app/            # Go application (HTTP server, downloader, uploader)
│   ├── http.go              # POST /upload endpoint
│   ├── controller.go        # Download + upload orchestration
│   ├── downloader.go        # yt-dlp wrapper
│   ├── uploader_biliup.go   # biliup wrapper
│   └── store.go             # SQLite persistence
├── ml-service/              # Python ML + discovery service
│   ├── app/
│   │   ├── llm/backend.py          # LLM backend (Ollama/OpenAI/Anthropic)
│   │   ├── description.py          # Description generation + title translation
│   │   ├── bilibili_subtitle.py    # Subtitle upload to Bilibili
│   │   ├── upload_client.py        # HTTP client for Go service
│   │   ├── bootstrap.py            # Database seeding
│   │   ├── prediction/             # Neural reranker, LLM predictor, LightGBM trainer
│   │   ├── embeddings/             # Title embeddings + vector store
│   │   ├── discovery/              # YouTube search, trending, LLM scorer
│   │   ├── web_rag/                # Bilibili + YouTube RAG search
│   │   ├── skills/                 # Self-improving skill framework
│   │   ├── scoring/                # Heuristic scoring + transportability
│   │   ├── search/                 # Search aggregator
│   │   └── outcomes/               # Outcome tracking
│   ├── real_run.py                 # Main pipeline entry point
│   ├── daily_job.py                # Scheduled daily job (VM deployment)
│   └── tests/                      # 607 tests
└── scripts/
    └── cookies.json                # Bilibili auth cookies
```
