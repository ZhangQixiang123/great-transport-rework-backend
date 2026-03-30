# Multi-User Chat Transport System — Architecture

## Problem Statement

Extend the single-user, CLI-driven YouTube-to-Bilibili transport system into a multi-user web application where users chat with an LLM to discover, evaluate, and upload videos — with proper isolation, concurrency, and model choice.

---

## Core Requirements

1. **User system** — Registration, login, per-user data (uploaded videos, conversations, preferences)
2. **Chat interface** — Multi-turn conversation with LLM, multiple simultaneous conversations per user
3. **LLM processing** — Concurrent messages from many users must not interfere; users choose model
4. **API gateway** — Single entry point, auth, rate limiting, routing to backend services
5. **Upload pipeline** — Existing Go download-to-upload pipeline, now scoped to users
6. **Data isolation** — Each user sees only their own videos, conversations, and upload history

---

## Architecture Overview

```
                        ┌─────────────────┐
                        │   Vue.js SPA    │
                        │   (Browser)     │
                        └────────┬────────┘
                                 │ HTTPS + WSS
                        ┌────────▼────────┐
                        │   API Gateway   │  ← Auth, Sentinel rate limit, routing
                        │   (Go)          │
                        └──┬─────┬─────┬──┘
                           │     │     │
              ┌────────────┘     │     └────────────┐
              │                  │                   │
     ┌────────▼────────┐ ┌──────▼───────┐ ┌────────▼────────┐
     │  Chat Service   │ │Upload Service│ │  Auth Service   │
     │  (WebSocket)    │ │(existing Go) │ │  (Go)           │
     └────────┬────────┘ └──────┬───────┘ └────────┬────────┘
              │                 │                   │
              │ publish         │                   │
     ┌────────▼────────┐       │          ┌────────▼────────┐
     │  Message Queue  │       │          │    Database     │
     │  (RabbitMQ)     │       │          │  (PostgreSQL)   │
     └────────┬────────┘       │          └─────────────────┘
              │ consume         │
     ┌────────▼────────┐       │
     │  LLM Workers    │       │
     │  (Python pool)  │───────┘ (submit upload jobs)
     └─────────────────┘
```

---

## Component Design

### 1. API Gateway (Go — built into existing binary)

Single entry point for all client requests. Handles authentication, rate limiting, routing.

Built into the existing Go binary using `http.NewServeMux()` with middleware layers:

```
Request → AuthMiddleware → RateLimitMiddleware → Route
                                                   ├── /api/auth/*    → AuthHandler
                                                   ├── /api/chat/*    → ChatService (proxy to Python or direct)
                                                   ├── /api/upload/*  → UploadService (existing)
                                                   ├── /ws            → WebSocket upgrade
                                                   └── /*             → Static files (Vue dist)
```

**Auth middleware**: Extract JWT from `Authorization` header or cookie, validate, inject `userID` into request context. Reject unauthenticated requests (except `/api/auth/*`).

**Rate limiting**: Per-user, per-endpoint via [sentinel-golang](https://github.com/alibaba/sentinel-golang) (in-process, no external dependency):
- Chat messages: 10/min per user
- Upload submissions: 5/hour per user
- Search: 20/min per user

Sentinel also provides circuit breaking (e.g. if Ollama is down, fail fast instead of queuing) and flow control (adaptive throttling under load).

**Rationale**: Go reverse proxy over Nginx/Kong — single binary, full control, already have HTTP server. Sentinel over Redis for rate limiting — in-process, zero network overhead, plus circuit breaking for free.

---

### 2. Auth Service (JWT with refresh tokens)

**User model**:
```sql
users
├── id (PK, SERIAL)
├── username (UNIQUE)
├── email (UNIQUE)
├── password_hash (bcrypt)
├── display_name
├── role (admin / user)
├── preferred_model (e.g. "ollama/qwen2.5:7b")
├── bilibili_uid (optional)
├── openai_api_key_encrypted (AES-256)
├── anthropic_api_key_encrypted (AES-256)
├── created_at
└── last_login_at
```

**Auth flow**:
- Access token: short-lived (15 min), contains `userID`, `role`, `preferred_model`
- Refresh token: long-lived (7 days), stored in DB, revocable
- For SPA: access token in memory (not localStorage), refresh token in httpOnly cookie
- JWT secret from env variable

**Why JWT**: WebSocket connections need auth too. JWT is validated in-memory without DB lookup on every message.

**Endpoints**:
- `POST /api/auth/register` — create user
- `POST /api/auth/login` — returns access token + sets refresh cookie
- `POST /api/auth/refresh` — exchange refresh token for new access token
- `POST /api/auth/logout` — revoke refresh token

---

### 3. Chat Service (WebSocket)

Manages WebSocket connections, routes messages, delivers LLM responses. Does NOT process LLM calls — publishes to RabbitMQ.

**Connection lifecycle**:
1. Client connects to `/ws` with JWT in query param or first message
2. Server validates JWT, associates connection with `userID`
3. Client sends chat messages (JSON), server publishes to RabbitMQ
4. LLM worker consumes message, processes it, publishes response to RabbitMQ reply queue
5. Chat service consumes response, pushes to correct WebSocket connection

**Connection registry**:
```go
type Hub struct {
    // userID → set of connections (user may have multiple tabs)
    clients map[int64]map[*Client]bool
    mu      sync.RWMutex
}
```

**Message routing**: Each message includes `conversation_id`. Responses routed to all connections for that user (multi-tab sync).

**Why separate from LLM processing**: LLM can take 30+ seconds. WebSocket thread must stay free for ping/pong, new conversations, cancellation. Queue decouples connection management from processing.

---

### 4. Message Queue (RabbitMQ)

**Why RabbitMQ over Redis Streams**: LLM calls are slow (10-30s), can fail, and need reliable delivery. RabbitMQ is purpose-built for this:
- **Dead letter queues** — failed LLM calls automatically routed to retry/dead queue instead of silently lost
- **Automatic redelivery** — if a worker crashes mid-processing, the message is redelivered to another worker without manual XPENDING/XCLAIM handling
- **Per-message TTL** — stale requests auto-expire (user closed the tab 5 min ago)
- **Priority queues** — premium users can get faster processing (future)
- **Backpressure** — if workers are overwhelmed, RabbitMQ handles it gracefully (Redis would just accumulate memory)
- **Durable queues** — survive broker restarts without configuration gymnastics

**Queue topology**:
```
Exchanges:
  chat.requests  (direct)  → queue: chat.requests.queue  (consumed by Python LLM workers)
  chat.responses (topic)   → queue: chat.responses.{go-instance-id}  (consumed by Go chat service)
  upload.status  (topic)   → queue: upload.status.{go-instance-id}   (consumed by Go chat service)

Dead letter:
  chat.requests.dlx (fanout) → queue: chat.requests.dead  (for inspection/retry)
```

**Request message** (published to `chat.requests`):
```json
{
    "conversation_id": 123,
    "user_id": 42,
    "message_type": "chat",
    "content": "Transport this cooking video",
    "context": "{ ... serialized state ... }",
    "model": "ollama/qwen2.5:7b",
    "reply_to": "chat.responses.go-1",
    "correlation_id": "msg-uuid-here",
    "timestamp": "2026-03-16T10:00:00Z"
}
```

**Response message** (published to `chat.responses`):
```json
{
    "conversation_id": 123,
    "user_id": 42,
    "correlation_id": "msg-uuid-here",
    "messages": [
        { "role": "assistant", "type": "text", "content": "Found 5 videos..." },
        { "role": "assistant", "type": "candidates", "data": [...] }
    ],
    "next_state": "presenting_candidates",
    "updated_context": "{ ... }"
}
```

**Concurrency**: N LLM worker processes (configurable), each consuming from `chat.requests.queue` with `prefetch_count=1` (process one message at a time per worker). RabbitMQ round-robins messages across workers. Ack after processing completes — if worker dies, message redelivers automatically.

**Per-conversation ordering**: Use `x-consistent-hash` exchange or conversation-id-based routing key so messages for the same conversation land on the same worker. This prevents race conditions within a conversation.

---

### 5. LLM Workers (Python standalone processes)

Consume from RabbitMQ, call appropriate LLM backend, publish results back. Long-running worker processes, NOT request/response servers.

```python
class LLMWorker:
    def __init__(self, worker_id, amqp_url, db_url):
        self.connection = pika.BlockingConnection(pika.URLParameters(amqp_url))
        self.channel = self.connection.channel()
        self.channel.basic_qos(prefetch_count=1)  # one message at a time
        self.db = Database(db_url)
        self.backends = {}

    def run(self):
        self.channel.basic_consume(
            queue="chat.requests.queue",
            on_message_callback=self.on_message
        )
        self.channel.start_consuming()

    def on_message(self, ch, method, properties, body):
        msg = json.loads(body)
        try:
            backend = self.get_or_create_backend(msg["model"])
            handler = ChatHandler(backend, self.db)
            response = handler.step(
                conversation_id=msg["conversation_id"],
                state=msg["context"]["state"],
                user_message=msg["content"],
                context=msg["context"]
            )
            # Publish response back via reply_to queue
            ch.basic_publish(
                exchange="chat.responses",
                routing_key=msg.get("reply_to", ""),
                body=json.dumps({
                    "conversation_id": msg["conversation_id"],
                    "user_id": msg["user_id"],
                    "correlation_id": msg.get("correlation_id"),
                    **response
                })
            )
            ch.basic_ack(delivery_tag=method.delivery_tag)
        except Exception as e:
            # Nack with requeue=False sends to dead letter queue
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
```

**Model routing per message**:
- `ollama/*` → OllamaBackend (local, free)
- `openai/*` → CloudBackend with user's OpenAI key
- `anthropic/*` → CloudBackend with user's Anthropic key

**Scaling**: 1-2 workers per GPU for Ollama, more for cloud API calls.

**FastAPI sidecar** for non-chat operations:
- `GET /models` — list available models
- `POST /search/youtube` — direct search (admin tools)
- Health checks

---

### 6. Database (PostgreSQL for multi-user, SQLite kept for ML data)

**Why PostgreSQL**: Multiple processes need concurrent write access. SQLite's single-writer is a bottleneck with multiple users. PostgreSQL handles concurrent connections natively, has connection pooling, full-text search.

**Migration strategy**: Keep existing SQLite for ML training data (competitor_channels, competitor_videos, etc.) — read-heavy, single-writer. New multi-user tables go in PostgreSQL. Go backend connects to both.

**Schema** (new PostgreSQL tables):

```sql
-- Auth
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    display_name VARCHAR(100) NOT NULL DEFAULT '',
    role VARCHAR(20) NOT NULL DEFAULT 'user',
    preferred_model VARCHAR(100) DEFAULT 'ollama/qwen2.5:7b',
    bilibili_uid VARCHAR(50),
    openai_api_key_encrypted TEXT,
    anthropic_api_key_encrypted TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    last_login_at TIMESTAMP
);

CREATE TABLE refresh_tokens (
    token VARCHAR(255) PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Conversations
CREATE TABLE conversations (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title VARCHAR(255),
    state VARCHAR(50) NOT NULL DEFAULT 'idle',
    context_json JSONB DEFAULT '{}',
    model VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE messages (
    id SERIAL PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL,    -- user, assistant, system
    content TEXT NOT NULL,
    message_type VARCHAR(30) DEFAULT 'text',
    metadata_json JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Upload jobs (extends existing concept with user scoping)
CREATE TABLE upload_jobs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    conversation_id INTEGER REFERENCES conversations(id),
    video_id VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    title TEXT,
    description TEXT,
    tags TEXT,
    bilibili_bvid VARCHAR(20),
    download_files JSONB,
    subtitle_status VARCHAR(20) DEFAULT 'pending',
    error_message TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- User's video library
CREATE TABLE user_videos (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    youtube_video_id VARCHAR(20) NOT NULL,
    bilibili_bvid VARCHAR(20),
    title TEXT NOT NULL,
    description TEXT,
    status VARCHAR(20) NOT NULL,
    views_at_upload INTEGER,
    uploaded_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Analytics
CREATE TABLE video_performance_snapshots (
    id SERIAL PRIMARY KEY,
    user_video_id INTEGER NOT NULL REFERENCES user_videos(id) ON DELETE CASCADE,
    bilibili_views INTEGER,
    bilibili_likes INTEGER,
    bilibili_coins INTEGER,
    bilibili_favorites INTEGER,
    bilibili_danmaku INTEGER,
    recorded_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE user_activity_log (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    action VARCHAR(50) NOT NULL,
    metadata_json JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Indices
CREATE INDEX idx_conversations_user ON conversations(user_id, updated_at DESC);
CREATE INDEX idx_messages_conversation ON messages(conversation_id, created_at);
CREATE INDEX idx_upload_jobs_user ON upload_jobs(user_id, created_at DESC);
CREATE INDEX idx_user_videos_user ON user_videos(user_id, created_at DESC);
CREATE INDEX idx_performance_video ON video_performance_snapshots(user_video_id, recorded_at);
CREATE INDEX idx_activity_user ON user_activity_log(user_id, created_at DESC);
```

---

### 7. Upload Service (existing Go backend, extended)

**Changes needed**:
- `upload_jobs` table moves to PostgreSQL with `user_id` and `conversation_id`
- Pipeline publishes status updates to RabbitMQ `upload.status` exchange (not just DB writes)
- Chat service consumes from its `upload.status.{instance-id}` queue, routes to WebSocket

**Status update flow**:
```
Pipeline stage changes → Write to PostgreSQL
                       → Publish to RabbitMQ "upload.status" exchange
                       → Go chat service consumes → Routes to user's WebSocket
```

---

## Chat Flow (State Machine)

```
User: "I want to transport a cooking video from MrBeast"
                    │
                    ▼
    ┌──────────────────────────────┐
    │  INTERPRETING                │  LLM extracts: topic, channel
    │  (LLM call #1)              │  Generates search queries
    └──────────────┬───────────────┘
                   │
                   ▼
    ┌──────────────────────────────┐
    │  CHECKING_BILIBILI           │  Search Bilibili for duplicates
    │  (Bilibili API)              │  Check if already transported
    └──────────────┬───────────────┘
                   │
          ┌────────┴────────┐
          │ saturated       │ not saturated
          ▼                 ▼
    "Already exists"     ┌──────────────────────────────┐
    → back to chat       │  SEARCHING_YOUTUBE + SCORING  │
                         └──────────────┬───────────────┘
                                        │
                                        ▼
    ┌──────────────────────────────────────────────────────┐
    │  PRESENTING_CANDIDATES                                │
    │  Top 5 videos: title, views, duration, thumbnail,    │
    │  transportability score                                │
    │  User: approve / reject / refine                      │
    └──────────────────────┬───────────────────────────────┘
                           │ user approves
                           ▼
    ┌──────────────────────────────┐
    │  GENERATING_METADATA         │  LLM generates Chinese title,
    │  (LLM call #2)              │  description
    └──────────────┬───────────────┘
                   │
                   ▼
    ┌──────────────────────────────┐
    │  REVIEWING_METADATA          │  User can edit or confirm
    └──────────────┬───────────────┘
                   │ confirmed
                   ▼
    ┌──────────────────────────────┐
    │  UPLOADING                   │  Submit to Go pipeline
    │                              │  Real-time progress via WebSocket
    └──────────────┬───────────────┘
                   │
                   ▼
    ┌──────────────────────────────┐
    │  DONE                        │  Bilibili link, saved to library
    └──────────────────────────────┘
```

---

## Model Selection

User preference stored in `users.preferred_model`, overridable per conversation.

**Available models** (configured server-side):
| ID | Name | Tier |
|----|------|------|
| `ollama/qwen2.5:7b` | Qwen 2.5 7B (Local) | free |
| `ollama/qwen2.5:32b` | Qwen 2.5 32B (Local) | free |
| `openai/gpt-4o-mini` | GPT-4o Mini | paid (BYOK) |
| `openai/gpt-4o` | GPT-4o | premium (BYOK) |
| `anthropic/claude-sonnet-4-5` | Claude Sonnet 4.5 | paid (BYOK) |
| `anthropic/claude-opus-4-6` | Claude Opus 4.6 | premium (BYOK) |

**BYOK (Bring Your Own Key)**: Local Ollama models are free. For OpenAI/Anthropic, users provide their own API key (stored AES-256 encrypted). No cost to service operator.

---

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Bilibili account | Single service account | Simpler cookie management, consistent channel |
| LLM cost | Free tier (Ollama) + BYOK | No operator cost for cloud LLM |
| Batch pipeline | Keep `real_run.py` as admin-only CLI | Admin batch + user chat coexist |
| API Gateway | Go built-in | Single binary, reuse existing HTTP server |
| Auth | JWT access + refresh tokens | Stateless, good for WebSocket |
| Rate limiting | sentinel-golang (in-process) | No external dependency, also gives circuit breaking |
| Chat transport | WebSocket (gorilla/websocket) | Bidirectional, multi-turn |
| Message queue | RabbitMQ | Dead letter queues, auto-redelivery, backpressure, durable — proper MQ for slow LLM calls |
| LLM workers | Python standalone processes | Reuse existing LLM/search code |
| Database | PostgreSQL (multi-user) + SQLite (ML) | Concurrent writes + keep existing ML pipeline |
| Frontend | Vue 3 + Vite + Pinia | SPA with real-time updates |
| Deployment | Docker Compose | Single command to bring up all services |

---

## Deployment

### Docker Compose (single machine)
```
docker-compose:
  ├── go-backend       (API gateway + Sentinel rate limit + chat service + upload pipeline)
  ├── python-workers   (2-3 LLM worker processes)
  ├── python-api       (FastAPI sidecar: models, health)
  ├── rabbitmq         (message queue with management UI on :15672)
  ├── postgresql       (users, conversations, jobs)
  ├── ollama           (local LLM, optional)
  └── nginx            (TLS termination, serve Vue.js static files)
```

Note: No Redis needed. Rate limiting is handled in-process by Sentinel. Message queue is RabbitMQ.

### Multi-machine (production)
- Machine 1: Go backend + Nginx
- Machine 2: Python workers (with GPU for Ollama)
- Machine 3: RabbitMQ + PostgreSQL (or managed services)
- Vue.js from CDN or Nginx

---

## Frontend Views

### User Views
- **Chat** — main interaction, multi-conversation sidebar
- **My Videos** — grid/list of uploaded videos with Bilibili links, status, views
- **Settings** — display name, preferred model, API keys

### Admin Views
- **Dashboard** — global stats (total uploads, active users, success rate, queue depth)
- **User Management** — list users, roles, activity
- **Upload Monitor** — all active/recent upload jobs
- **Analytics** — charts: uploads over time, views per video, model usage

---

## New Go Dependencies

| Package | Purpose |
|---------|---------|
| `github.com/lib/pq` | PostgreSQL driver |
| `github.com/golang-jwt/jwt/v5` | JWT token creation/validation |
| `github.com/gorilla/websocket` | WebSocket connections |
| `github.com/rabbitmq/amqp091-go` | RabbitMQ client (AMQP 0.9.1) |
| `github.com/alibaba/sentinel-golang` | In-process rate limiting + circuit breaking |
| `golang.org/x/crypto` | bcrypt password hashing |

## New Python Dependencies

| Package | Purpose |
|---------|---------|
| `pika` | RabbitMQ consumer (AMQP 0.9.1) |
| `psycopg2-binary` or `asyncpg` | PostgreSQL access from workers |
| `cryptography` | AES-256 API key decryption |

---

## Open Questions for Discussion

1. **SQLite migration scope** — Should we migrate ALL existing tables to PostgreSQL (cleaner, one DB) or keep SQLite for ML data (less migration work)?

2. **WebSocket vs SSE** — WebSocket gives bidirectional comms, but SSE is simpler if we only need server-to-client streaming. Chat needs client-to-server too, so WebSocket seems right. Agree?

3. **Worker specialization** — Should Ollama workers be separate from cloud API workers, or should all workers handle all models?

4. **Admin user bootstrap** — How should the first admin user be created? CLI command? Environment variable seed? Auto-create on first startup?

5. **Existing upload_jobs** — The current SQLite `upload_jobs` table is used by the Go pipeline. Should we:
   - (a) Move it entirely to PostgreSQL and update all Go code, or
   - (b) Keep SQLite for pipeline internals and sync status to PostgreSQL for the user-facing API?

6. **Frontend** — Vue 3 is specified. Should the frontend live in this repo (monorepo) or a separate repo?

7. **Subtitle pipeline** — Keep as-is (non-blocking post-upload) or make it user-configurable per upload?
