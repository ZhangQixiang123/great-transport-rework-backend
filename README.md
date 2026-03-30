# Great Transport — Persona-Centric YouTube-to-Bilibili Pipeline

Automated system that discovers YouTube videos through persona-driven strategies, generates culturally-adapted Chinese content with LLM personas, and uploads to Bilibili with bilingual subtitles and persona-voiced danmaku.

## Architecture

```
Python (ml-service/)                              Go (internal/app/)
┌────────────────────────────────┐               ┌───────────────────────────────┐
│  PersonaOrchestrator           │               │  HTTP server (:8081)          │
│                                │               │                               │
│  1. Strategy Generation (LLM)  │               │  POST /upload → job queue     │
│  2. Market Analysis (Bilibili) │               │    ├─ yt-dlp download         │
│  3. YouTube Search + Scoring   │   HTTP POST   │    ├─ biliup upload → BVID    │
│  4. Transportability Check     │  ───────────► │    └─ subtitle pipeline       │
│  5. Copy Generation (LLM)     │               │         ├─ whisper transcribe  │
│  6. Human Review               │               │         ├─ Google Translate    │
│  7. Upload to Go service       │               │         └─ draft for review   │
│                                │               │                               │
│  Annotation Server (:8082)     │   danmaku +   │  POST /subtitle-approve       │
│    └─ LLM persona comments     │ ◄─── CC sub ──│    ├─ bilingual CC upload     │
└────────────────────────────────┘               │    └─ danmaku post            │
                                                 └───────────────────────────────┘
```

**Python** handles intelligence: persona-driven discovery, LLM strategy generation, scoring, transportability filtering, title/description generation, and subtitle annotation.

**Go** handles media: downloading (yt-dlp), uploading (biliup), subtitle generation (whisper + translate), and Bilibili API interactions (CC subtitles, danmaku).

## Persona System

Personas are pluggable discovery profiles that own their voice, strategies, and content preferences.

### SarcasticAI (active)

Tsundere AI persona — condescending about humans, secretly fascinated. Generates titles and descriptions in 傲娇 style with cute insults (笨蛋、杂鱼、废物).

- **6 strategies**: gaming_deep_dive, social_commentary, geopolitics_hot_take, challenge_experiment, global_trending_chinese_angle, surveillance_dashcam
- **Tsundere intensity levels**: high (gaming, dashcam) / mid (social, trending) / low (geopolitics)
- **10 few-shot examples** with varying attack angles
- **Subtitle annotations**: chain-of-thought analysis of video rhetoric → targeted danmaku comments

## Quick Start

### Dry run (preview only)

```bash
cd ml-service
.venv/Scripts/python real_run.py --backend ollama --dry-run
```

### Full upload pipeline

```bash
# Terminal 1: Go upload service
go run ./cmd/yttransfer --http-addr :8081 \
  --biliup-cookie scripts/cookies.json \
  --biliup-binary ml-service/.venv/Scripts/biliup.exe \
  --annotation-url http://127.0.0.1:8082

# Terminal 2: Annotation server
cd ml-service
.venv/Scripts/python -m app.annotation_server --port 8082 --persona sarcastic_ai --backend ollama

# Terminal 3: Discovery + upload
cd ml-service
.venv/Scripts/python real_run.py --backend ollama --upload --go-url http://localhost:8081
```

### CLI options (real_run.py)

| Flag | Default | Description |
|------|---------|-------------|
| `--backend` | `ollama` | LLM backend: `ollama`, `openai`, `anthropic` |
| `--model` | per-backend | LLM model override (default: `qwen2.5:14b` for ollama) |
| `--max-queries` | `5` | Search queries per run |
| `--top-n` | `3` | Top candidates for upload |
| `--upload` | off | Enable upload (requires Go server) |
| `--dry-run` | off | Preview payloads without uploading |
| `--no-review` | off | Skip human review (auto-approve) |
| `--go-url` | `http://localhost:8080` | Go service URL |

## Go API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/upload` | Submit video job (202 Accepted) |
| GET | `/upload/status?id=N` | Poll job status |
| GET | `/upload/jobs?limit=50` | List recent jobs |
| GET | `/upload/uploaded-ids` | All uploaded video IDs (dedup) |
| POST | `/upload/retry-subtitle?id=N` | Regenerate subtitle draft |
| GET | `/upload/subtitle-preview?id=N` | Preview subtitle + danmaku draft |
| POST | `/upload/subtitle-approve?id=N` | Publish subtitles + post danmaku |

## Subtitle Pipeline

After video upload, the Go service generates bilingual subtitles with persona annotations:

```
1. Whisper transcribe → English SRT
2. Google Translate → Chinese SRT
3. LLM annotation → persona danmaku (chain-of-thought: analyze rhetoric → target absurdities)
4. Save draft → subtitle_status = "review"
5. Human preview via GET /upload/subtitle-preview
6. Approve via POST /upload/subtitle-approve
   ├─ Upload bilingual CC (中文 + English per entry)
   └─ Post danmaku (top-fixed, orange-red, persona voice)
```

Annotation quality control:
- Auto-scales count by duration (~1 per 30s, min 2, max 8)
- Chain-of-thought: LLM must analyze video logic before writing comments
- Targets contradictions, rhetoric traps, vanity hooks — not empty insult spam
- Dedicated system prompt for annotations (separate from title/desc persona)

## Pipeline Phases

### Phase 1: Strategy Generation
`StrategyGenerationSkill` generates YouTube search queries from active strategies. Self-improving via prompt versioning and yield reflection.

### Phase 2: Market Analysis
`MarketAnalysisSkill` checks Bilibili saturation per query. Filters oversaturated niches.

### Phase 3: YouTube Search + Scoring
```
score = (engagement × w1 + view_signal × w2 + opportunity × w3 + duration × w4) × category_bonus
```
Category bonuses from persona's `CONTENT_AFFINITY`. Parameters bootstrapped from historical data.

### Phase 4: Transportability Check
**Hard filters** (regex): Chinese leadership, CCP/separatism, antisemitism, hate speech, explicit content.
**LLM scoring**: persona-fit evaluation (configurable threshold, default 0.3).

### Phase 5: Copy Generation
LLM generates Chinese title + description in persona voice. Uses `SYSTEM_PROMPT` + `STRATEGY_HINTS` + few-shot examples biased by tsundere intensity.

### Phase 6: Human Review
Interactive approval loop. Reviewer sees title, description, scores, strategy context.

### Phase 7: Upload
Submits to Go service → yt-dlp download → biliup upload → extract BVID → trigger subtitle pipeline.

## Requirements

- Python 3.12+ with venv (`ml-service/.venv`)
- Go 1.22+
- `yt-dlp`, `ffmpeg` in PATH
- [`biliup`](https://github.com/biliup/biliup) — `ml-service/.venv/Scripts/biliup.exe`
- Bilibili cookies: `scripts/cookies.json` (run `biliup --user-cookie cookies.json login`)
- LLM: Ollama with `qwen2.5:14b` (9GB VRAM), or OpenAI/Anthropic API key
- Optional: YouTube Data API key for search

## Project Structure

```
├── cmd/yttransfer/                 # Go CLI entry point
├── internal/app/
│   ├── http.go                     # HTTP API (7 endpoints)
│   ├── queue.go                    # 3-stage job pipeline (feed → download → upload)
│   ├── subtitle_pipeline.go        # Whisper + translate + annotation + review flow
│   ├── bilibili_subtitle.go        # BCC format, bilingual merge, danmaku posting
│   ├── downloader.go               # yt-dlp wrapper
│   ├── uploader_biliup.go          # biliup wrapper with per-video metadata
│   ├── store.go                    # SQLite (upload_jobs, uploads, subtitle drafts)
│   └── translate.go                # Google Translate (free endpoint)
├── ml-service/
│   ├── app/
│   │   ├── personas/
│   │   │   ├── protocol.py                 # Persona interface
│   │   │   ├── sarcastic_ai/
│   │   │   │   ├── __init__.py             # 7-phase orchestrator
│   │   │   │   ├── config.py               # Identity, affinity, thresholds
│   │   │   │   ├── prompts.py              # SYSTEM_PROMPT, few-shot, STRATEGY_HINTS
│   │   │   │   └── strategies.py           # 6 strategies + validation
│   │   │   └── _shared/
│   │   │       ├── subtitle_annotator.py   # Chain-of-thought annotation generation
│   │   │       ├── transportability.py     # Hard filters + LLM persona-fit
│   │   │       ├── scoring.py              # Heuristic scoring formula
│   │   │       └── review.py               # Interactive approval loop
│   │   ├── llm/backend.py                  # Ollama / OpenAI / Anthropic
│   │   ├── skills/
│   │   │   ├── strategy_generation.py      # Query generation skill
│   │   │   └── market_analysis.py          # Bilibili saturation check
│   │   ├── tags.py                         # Bilibili tag aggregation
│   │   ├── description.py                  # Persona copy generation
│   │   ├── upload_client.py                # Go service HTTP client
│   │   ├── annotation_server.py            # HTTP server for Go subtitle pipeline
│   │   ├── db/database.py                  # SQLite schema + migrations
│   │   └── bootstrap.py                    # DB seeding (strategies, scoring params)
│   ├── real_run.py                         # Main entry point
│   └── tests/                              # 638 tests
├── scripts/
│   ├── cookies.json                        # Bilibili auth
│   └── whisper_transcribe.py               # Whisper SRT generation
└── docs/
    └── persona-centric-refactor.md         # Architecture design doc
```

## Testing

```bash
cd ml-service
.venv/Scripts/python -m pytest tests/ -v
# 638 tests, all passing
```

## Job Status Flow

```
Upload:    pending → downloading → uploading → completed / failed
Subtitle:  pending → generating → review → completed / failed
                                    ↑
                          human approves via API
```
