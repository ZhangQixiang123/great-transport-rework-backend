# Python 编排器重构方案

> Python 保留为有状态编排器 + 唯一前端入口，Go 保留为上传工具服务（独立管理视频 DB）。
> 替代已废弃的 `go-orchestrator-architecture.md`。

---

## 1. 核心变更

### 1.1 架构对比

```
旧架构（当前，双入口）

  Frontend (:5173)
      ├── /api/go/* ──→ Go (:8081)      上传/字幕/弹幕
      └── /api/py/* ──→ Python (:8000)   Pipeline/技能/反思
                           │
                     Python 也通过 HTTP 调 Go

新架构（Python 单入口 + SSE）

  Frontend (:5173)
      └── /api/* ──→ Python (:8000)  唯一入口
                       ├── Pipeline 状态（内存，会话级）
                       ├── SSE 流式推送进度
                       ├── Skill/Review CRUD（data.db）
                       ├── 上传管理（代理 Go 端点）
                       └── Go (:8081)  上传工具服务
                             ├── 下载（yt-dlp）
                             ├── 上传（biliup）→ metadata.db
                             └── 字幕（whisper + translate + danmaku）
```

### 1.2 设计原则

| 原则 | 说明 |
|------|------|
| **前端单入口** | 前端只与 Python 通信，消除双代理 |
| **SSE 替代轮询** | Pipeline 进度通过 Server-Sent Events 实时推送 |
| **多 Session** | 多个用户可同时独立运行 pipeline，各自有独立会话状态 |
| **DB 分离** | Python 管 `data.db`（策略/技能/审核），Go 管 `metadata.db`（上传任务/字幕）——各管各的领域 |
| **会话状态在内存** | Pipeline 中间状态（candidates、闭包、审核进度）留在进程内存 |
| **Go 保持自治** | Go 的 job queue、崩溃恢复、字幕 pipeline 不受影响 |

### 1.3 为什么不用 Go Orchestrator 方案

已废弃的 `go-orchestrator-architecture.md` 提出 Go 成为唯一 HTTP 入口 + DB 状态机，Python 退化为无状态 CLI。分析后发现根本性问题：

**Pipeline 中间状态不适合 DB 化：**
- `_regenerate_fn` 是绑定了 LLM backend 的闭包——无法序列化
- 策略选择用 `threading.Event` 同步等待，改成 DB 轮询变成复杂链路
- 这些是会话状态，不是业务数据

**Go 写大量新代码但功能不变：**
- 10 个 CRUD 端点从 Python 移到 Go——纯粹的搬运
- Go 实现 Pipeline 状态机（6 阶段 + JSON 序列化）——复杂度高、收益低
- 拆 Python 为 5 个 CLI——增加进程开销

**现状已经是 "Python 编排 + Go 工具"：**
- Python 启停 Go 进程（`/go/start`, `/go/stop`）
- Python 向 Go 提交上传（`submit_upload()`）
- Go 调 Python 只有 `annotate_cli` 一处
- Pipeline 全部逻辑在 Python

### 1.4 为什么保留 DB 分离

Go 的上传 pipeline（download→upload→subtitle）是自治的长时间工作流：
- Job queue 用 goroutine 频繁更新 `upload_jobs` 状态
- `RecoverOrphanedJobs()` 需要启动时直接操作 DB
- 字幕 draft 读写密集（几百 KB JSON）
- 这些操作如果跨进程会增加延迟和复杂度

跨库关联的问题通过 Python 调 Go HTTP 解决（Loop 2 现在就是这么做的）。

---

## 2. 具体变更

### 2.1 SSE 替代轮询

当前：前端每 2 秒 `GET /api/py/status` 轮询 pipeline 状态。

新方案：

```python
@app.get("/api/pipeline/events")
async def pipeline_events(request: Request):
    """SSE — 前端建立一次连接，持续接收进度更新。"""
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            event = await state.wait_for_update()
            yield f"data: {json.dumps(event)}\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

前端：
```typescript
const es = new EventSource('/api/pipeline/events');
es.onmessage = (e) => {
    const status = JSON.parse(e.data);
    // 实时更新 UI
};
```

优势：
- 零延迟感知状态变化（vs 最多 2 秒）
- regenerate 等中间状态可实时推送
- 前端断线自动重连（EventSource 内置）
- 保留 `GET /api/pipeline/status` 作为 fallback（某些代理不支持 SSE）

### 2.2 Pipeline asyncio 化

当前：`threading.Thread` + `threading.Event.wait(timeout=1800)`。

改为 asyncio，消除 30 分钟超时限制：

```python
class PipelineSession:
    """一次 pipeline 运行的会话状态。"""

    def __init__(self):
        self.phase: str = "idle"
        self.candidates: list[dict] = []
        self.error: str | None = None
        self._update_event = asyncio.Event()
        self._strategy_event = asyncio.Event()

    def notify(self, phase: str, **data):
        """更新状态并通知 SSE 订阅者。"""
        self.phase = phase
        for k, v in data.items():
            setattr(self, k, v)
        self._update_event.set()
        self._update_event = asyncio.Event()

    async def wait_for_update(self) -> dict:
        await self._update_event.wait()
        return self.to_dict()

    async def wait_for_strategy_selection(self) -> set[str]:
        """挂起直到前端选择策略。无超时。"""
        await self._strategy_event.wait()
        return self.selected_strategies
```

关键变化：
- 无超时限制——状态在内存，SSE 连接保持
- `_regenerate_fn` 闭包自然保留，不需要序列化
- `asyncio.Event` 替代 `threading.Event`

### 2.3 多 Session 支持

当前 `PipelineState` 是全局单例，只能跑一个 pipeline。改为 session map，多个用户独立运行：

```python
sessions: dict[str, PipelineSession] = {}

@app.post("/api/pipeline/start")
async def start_pipeline(req: StartRequest):
    session_id = str(uuid4())
    session = PipelineSession(session_id)
    sessions[session_id] = session
    asyncio.create_task(run_pipeline(session, req))
    return {"session_id": session_id}

@app.get("/api/pipeline/events/{session_id}")
async def pipeline_events(session_id: str, request: Request):
    session = sessions[session_id]
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            event = await session.wait_for_update()
            yield f"data: {json.dumps(event)}\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/api/pipeline/review/{session_id}/{candidate_id}")
async def review(session_id: str, candidate_id: str, decision: ReviewDecision):
    session = sessions[session_id]
    # 操作该 session 的 candidates...

@app.get("/api/sessions")
async def list_sessions():
    """列出所有活跃 session，供前端选择/恢复。"""
    return [
        {"session_id": s.session_id, "phase": s.phase, "created_at": s.created_at}
        for s in sessions.values()
    ]
```

每个 session 的生命周期：
```
创建 → pipeline 运行中（内存状态）→ 完成/出错 → 一段时间后清理
```

Session 清理策略：
- 完成/出错的 session 保留 1 小时后自动清理
- 前端可主动 `DELETE /api/pipeline/{session_id}` 清理
- Python 重启时所有 session 丢失（review 阶段通过快照恢复）

### 2.4 LLM 并发控制

多 session 同时运行时，LLM 调用需要限流。不需要拆分 LLM 为独立服务——在 Python 内加信号量即可：

```python
# 本地 Ollama 串行推理，限制并发
llm_semaphore = asyncio.Semaphore(2)  # 可配置

async def call_llm(backend, messages, **kwargs):
    """所有 LLM 调用经过信号量，防止多 session 同时轰炸。"""
    async with llm_semaphore:
        return await asyncio.to_thread(backend.chat, messages=messages, **kwargs)
```

不同 backend 的策略：
- **Ollama（本地）**：信号量 = 1-2，串行推理是瓶颈
- **OpenAI/Anthropic（云端）**：信号量 = 10+，API 天然支持并发
- 可通过启动参数 `--llm-concurrency N` 配置

Go 的上传队列**不需要改动**——多个 session 提交的上传任务自然排队进 Go 的串行 JobQueue。

### 2.6 Review 快照（轻量崩溃恢复）

Pipeline 各阶段中，唯一值得恢复的是 review（用户已花时间审核候选）。不需要完整状态机，只需快照：

```python
async def enter_review(self, candidates):
    # 保存候选列表到 DB（不含闭包，只含可序列化的数据）
    snapshot = [serialize_candidate(c) for c in candidates]
    self.db.save_review_snapshot(self.run_id, json.dumps(snapshot))
    self.notify("review", candidates=candidates)
    await self.wait_for_all_reviews()

# 启动时检查
def check_pending_review(self):
    snapshot = self.db.get_review_snapshot()
    if snapshot:
        # 恢复到审核阶段，无需重跑 search/scoring/copy
        return json.loads(snapshot)
    return None
```

其余阶段（策略生成、搜索、文案生成）崩溃后直接重跑，耗时短且无人工投入。

### 2.7 前端单入口

Python 代理 Go 的上传管理端点：

```python
# Python 代理 Go 的字幕/上传相关端点
GO_URL = "http://localhost:8081"

@app.get("/api/uploads")
async def list_uploads(limit: int = 50):
    """代理 Go 的 /upload/jobs"""
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{GO_URL}/upload/jobs", params={"limit": limit})
        return r.json()

@app.get("/api/uploads/{job_id}/subtitle-preview")
async def subtitle_preview(job_id: int):
    async with httpx.AsyncClient() as client:
        r = await client.get(f"{GO_URL}/upload/subtitle-preview", params={"id": job_id})
        return r.json()

@app.post("/api/uploads/{job_id}/approve-subtitle")
async def approve_subtitle(job_id: int):
    async with httpx.AsyncClient() as client:
        r = await client.post(f"{GO_URL}/upload/subtitle-approve", params={"id": job_id})
        return r.json()

# ... 同理代理 retry-subtitle, annotate, delete
```

前端 API：
```typescript
// 旧
const GO_BASE = '/api/go';
const PY_BASE = '/api/py';

// 新
const API_BASE = '/api';
```

### 2.8 端点设计

```
Session 管理
  GET  /api/sessions                                # 列出活跃 session
  DELETE /api/pipeline/{session_id}                  # 清理 session

Pipeline（有状态，按 session 隔离）
  POST /api/pipeline/start                          → 返回 {session_id}
  GET  /api/pipeline/{session_id}/status             # fallback polling
  GET  /api/pipeline/{session_id}/events             # SSE 推送
  GET  /api/pipeline/{session_id}/strategies         # 可选策略列表
  POST /api/pipeline/{session_id}/select-strategies
  POST /api/pipeline/{session_id}/review/{id}
  POST /api/pipeline/{session_id}/regenerate/{id}

Skill 管理（CRUD，Python 直接操作 data.db，全局共享）
  GET  /api/skills
  GET  /api/skills/{name}/versions
  POST /api/skills/{name}/update
  POST /api/skills/{name}/rollback
  POST /api/skills/reflect
  POST /api/skills/{name}/apply-reflection

数据采集（Python 操作 data.db + 调 Go HTTP，全局共享）
  POST /api/loop2/collect
  GET  /api/loop2/stats
  GET  /api/review-stats
  POST /api/annotation/feedback

上传管理（Python 代理 Go 端点，全局共享）
  GET  /api/uploads
  GET  /api/uploads/{id}/subtitle-preview
  POST /api/uploads/{id}/retry-subtitle
  POST /api/uploads/{id}/approve-subtitle
  POST /api/uploads/{id}/annotate
  DELETE /api/uploads/{id}
```

### 2.9 Go 服务保持不变

Go 的代码**不需要改动**。它继续：
- 监听 `:8081`，管理 `metadata.db`
- 运行 job queue（download→upload→subtitle）
- 提供上传/字幕相关 HTTP 端点

唯一的变化：**前端不再直连 Go**，改由 Python 代理。Go 成为 Python 的内部依赖。

Go 的启停由 Python 管理（现有的 `/go/start`、`/go/stop` 保留，但前端调的是 Python 端点）。

---

## 3. 数据库设计

### 3.1 保持分离

```
Python → data.db
  ├── strategies
  ├── strategy_runs
  ├── skills
  ├── skill_versions
  ├── review_decisions
  ├── annotation_feedback
  ├── scoring_params
  └── review_snapshots (新增，用于 Review 崩溃恢复)

Go → metadata.db
  ├── upload_jobs (+ persona_id, strategy_name 新增列)
  └── uploads
```

### 3.2 跨库关联

| 场景 | 数据流 |
|------|--------|
| Pipeline 提交上传 | Python `POST Go:/upload`，传 `persona_id` + `strategy_name` |
| Loop 2 采集 | Python `GET Go:/upload/jobs` 拿 bvid → 调 B站 API → 写 `data.db` |
| 前端查看上传列表 | Python 代理 `GET Go:/upload/jobs` |
| 策略通过率 | Python 直接查 `data.db` 的 `review_decisions` |

### 3.3 新增表

```sql
-- data.db: Review 崩溃恢复快照
CREATE TABLE IF NOT EXISTS review_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    candidates_json TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## 4. 与旧方案的对比

| 维度 | Go Orchestrator（已废弃） | Python 编排器（本方案） |
|------|--------------------------|----------------------|
| Go 代码变更 | 大 — 加 10+ 端点 + 状态机 | 无 — Go 代码不动 |
| Python 代码变更 | 大 — 拆为 5 个无状态 CLI | 中 — asyncio + SSE + 代理层 + session 管理 |
| 多用户 | 不支持（单 pipeline_runs 表） | 支持（session map，各自独立） |
| Pipeline 中间状态 | DB 序列化（不支持闭包） | 内存（自然保留闭包） |
| 崩溃恢复 | 完整（全阶段 DB 持久化） | 轻量（只恢复 review 快照） |
| 实时性 | 2 秒轮询 | SSE 零延迟 |
| LLM 并发 | 无控制 | 信号量限流，可配置 |
| DB 归属 | 合并为单 DB，Go 管 | 分离，各管各的 |
| 运行进程数 | 1（Go） | 2（Python + Go），但 Go 可由 Python 自动管理 |

---

## 5. 实施阶段

### Stage 1：前端单入口

- [ ] Python 加代理层：`/api/uploads/*` 代理到 Go `:8081`
- [ ] Pipeline 端点加 `/api` 前缀（统一路由）
- [ ] Skill/Loop2/ReviewStats 端点加 `/api` 前缀
- [ ] 前端 `api/index.ts` 统一 `API_BASE = '/api'`
- [ ] Vite 代理改为单入口（`/api` → Python :8000）
- [ ] Go HTTP 端点保留不变

**验证**：前端全部功能通过 Python 入口可用，不再直连 Go。

### Stage 2：多 Session + SSE + asyncio

- [ ] `PipelineState` 单例改为 `sessions: dict[str, PipelineSession]`
- [ ] `POST /api/pipeline/start` 返回 `session_id`
- [ ] 所有 pipeline 端点加 `{session_id}` 路径参数
- [ ] `GET /api/sessions` 列出活跃 session
- [ ] `_run_pipeline()` 从 threading 改为 asyncio task
- [ ] 加 SSE endpoint（`/api/pipeline/{session_id}/events`）
- [ ] 加 LLM 信号量（`asyncio.Semaphore`，`--llm-concurrency` 参数）
- [ ] 消除 30 分钟超时
- [ ] Session 清理（完成 1 小时后自动移除）
- [ ] 前端 PipelineProgress 改用 EventSource
- [ ] 前端启动 pipeline 后记住 session_id
- [ ] 保留 `GET /api/pipeline/{session_id}/status` 作为 fallback

**验证**：两个浏览器窗口同时独立运行 pipeline，互不干扰。

### Stage 3：Review 快照 + Go 元数据扩展

- [x] `review_snapshots` 表（按 session_id 索引）+ 启动时恢复逻辑
- [x] Go 端 `upload_jobs` 加 `persona_id` + `strategy_name` 列
- [x] Python `submit_upload()` 传递新字段
- [x] Loop 2 collector 用 `strategy_name` 关联

**验证**：Pipeline review 阶段崩溃后可恢复；Loop 2 可按策略关联数据。

### Stage 4：清理

- [x] 删除前端 `GO_BASE`/`PY_BASE` 双前缀
- [x] 删除 Vite 双代理配置
- [x] 更新 CLAUDE.md
- [x] 更新 fast-prototype-report.md

---

## 6. 风险与缓解

| 风险 | 缓解 |
|------|------|
| Python 代理层增加一跳延迟 | 上传管理端点不频繁，延迟可忽略 |
| SSE 在某些代理后断连 | EventSource 内置自动重连；保留 GET /status fallback |
| 两个服务仍需同时运行 | Python 自动管理 Go 生命周期（`/go/start`） |
| asyncio 改造影响现有 pipeline 逻辑 | 核心逻辑不变，只是换调度模型 |
| Go 挂了 Python 代理返回错误 | Python 代理层加健康检查 + 友好错误信息 |
| 多 session 内存占用 | 每个 session 的 candidates 占用很小（几十 KB）；完成后定时清理 |
| 多 session 同时调 LLM | 信号量限流；Ollama 本身串行，不会过载 |
| 多 session 同时提交上传 | Go 的 JobQueue 已是串行的，自然排队 |
