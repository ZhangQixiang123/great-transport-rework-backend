# Python 异步架构详解

## 进程模型

整个 Python 服务只有 **1 个进程、1 个事件循环线程、1 个线程池**：

```
┌─────────────────────────────────────────────────────────────────┐
│  Python 进程 (uvicorn)                                          │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  事件循环线程 (唯一, 永远不能阻塞)                          │  │
│  │                                                           │  │
│  │  职责:                                                    │  │
│  │  - 接收所有 HTTP 请求                                      │  │
│  │  - 维护 SSE 长连接                                         │  │
│  │  - 调度协程 (coroutine)                                    │  │
│  │  - 操作共享状态 (sessions dict, asyncio.Event)             │  │
│  │                                                           │  │
│  │  不能做的事:                                               │  │
│  │  - 任何超过几毫秒的阻塞操作                                 │  │
│  │    (否则所有请求/SSE/定时器全部冻结)                         │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  线程池 (默认 ~40 个 worker, 按需分配)                      │  │
│  │                                                           │  │
│  │  职责:                                                    │  │
│  │  - 执行 asyncio.to_thread() 提交的阻塞任务                 │  │
│  │  - LLM 调用 (可能耗时 30秒+)                               │  │
│  │  - YouTube API 搜索 (网络 I/O)                             │  │
│  │  - SQLite 读写 (极快, <1ms)                                │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## 为什么需要异步

旧代码用 `threading.Thread` 跑 pipeline，简单但有两个问题：

1. **无法做 SSE 推送** — `threading.Event` 只能在线程间通信，无法和 FastAPI 的 `StreamingResponse` 协作
2. **无法跨 session 协调** — 两个 pipeline 的 LLM 调用无法用信号量限流

异步模型让 pipeline 协程和 SSE 协程跑在同一个事件循环上，通过 `asyncio.Event` 直接通信。代价是所有阻塞调用必须用 `asyncio.to_thread()` 推到线程池。

## 一次完整 Pipeline 运行的数据流

```
浏览器                    事件循环线程                          线程池
  │                         │                                  │
  │  POST /api/pipeline/start                                  │
  │ ──────────────────────> │                                  │
  │                         │  session = PipelineSession()     │
  │                         │  sessions["abc"] = session       │
  │                         │  create_task(run_pipeline)  ①    │
  │  <── {session_id:"abc"} │                                  │
  │                         │                                  │
  │  GET /api/pipeline/abc/events  (SSE长连接)                  │
  │ ──────────────────────> │                                  │
  │                         │  ② pipeline_events() 协程启动     │
  │  <── data: {step:"idle"}│     yield 当前状态               │
  │                         │     await session.wait_for_update│
  │                         │     (挂起, 让出事件循环)          │
  │                         │                                  │
  │                         │  ③ run_pipeline() 协程继续:      │
  │                         │                                  │
  │                         │  db.connect(check_same_thread=F) │
  │                         │  session.notify(strategy_gen) ─┐ │
  │                         │                                │ │
  │                         │  ④ notify() 内部:              │ │
  │                         │     session.phase = strategy_gen│ │
  │                         │     _update_event.set() ────────┘ │
  │                         │     (唤醒 SSE 协程 ②)            │
  │                         │                                  │
  │  <── data:{step:"strategy_generation"}   (SSE推送)         │
  │                         │                                  │
  │                         │  ⑤ await to_thread(             │
  │                         │      bootstrap_strategies, db)   │
  │                         │  ──────────────────────────────> │
  │                         │  (事件循环空闲,可处理其他请求)     │ db.execute(...)
  │                         │  <────────────────────────────── │ 返回结果
  │                         │                                  │
  │                         │  ⑥ await to_thread(             │
  │                         │      strategy_skill.execute)     │
  │                         │  ──────────────────────────────> │
  │                         │  (事件循环空闲)                   │ backend.chat()
  │                         │                                  │ (LLM调用,30秒+)
  │                         │                                  │
  │                         │  同时可以处理其他请求:             │
  │  GET /api/skills        │                                  │
  │ ──────────────────────> │  ⑦ 立即处理, 返回结果            │
  │  <── [{name:...}]       │  (不受pipeline阻塞影响)          │
  │                         │                                  │
  │                         │  <────────────────────────────── │ LLM返回
  │                         │                                  │
  │                         │  session.notify(search)          │
  │  <── data:{step:"search"}                                  │
  │                         │                                  │
  │                         │  ⑧ for q in queries:            │
  │                         │    await to_thread(              │
  │                         │      search_youtube_videos, q)   │
  │                         │  ──────────────────────────────> │ HTTP→YouTube
  │                         │  <────────────────────────────── │
  │                         │                                  │
  │                         │  session.notify(review)          │
  │  <── data:{step:"review", candidates:[...]}                │
  │                         │                                  │
  │                         │  ⑨ await session._review_event  │
  │                         │     .wait()                      │
  │                         │  (协程挂起, 等用户审核)            │
  │                         │  (事件循环完全空闲)               │
  │                         │                                  │
  │  POST /api/pipeline/abc/review/vid1                        │
  │ ──────────────────────> │                                  │
  │                         │  ⑩ job["_approved"] = True      │
  │                         │  all_decided → _review_event.set│
  │                         │  (唤醒 ⑨ 的 run_pipeline)       │
  │  <── {all_decided:true} │                                  │
  │                         │                                  │
  │                         │  session.notify(uploading)       │
  │  <── data:{step:"uploading"}                               │
  │                         │                                  │
  │                         │  await to_thread(submit_upload)  │
  │                         │  ──────────────────────────────> │ HTTP→Go:8081
  │                         │  <────────────────────────────── │
  │                         │                                  │
  │                         │  session.notify(done)            │
  │  <── data:{step:"done", summary:{...}}                     │
  │                         │  SSE stream 结束                 │
```

## 关键机制详解

### `await` = 让出事件循环

```python
# run_pipeline 协程
result = await asyncio.to_thread(search_youtube_videos, query)
#        ^^^^^
#        此刻 run_pipeline 挂起
#        事件循环线程 空闲, 可以:
#          - 响应 /api/skills 请求
#          - 给 SSE 客户端发 keepalive
#          - 处理另一个 session 的 review
#        search 完成后, 事件循环把结果交回 run_pipeline, 继续执行
```

`await` 不是"等待"——是"让出控制权，别人做完了再叫我"。

### `asyncio.Event` = 协程间通信

```python
# 两个协程共享同一个 Event 对象:

# 协程A: run_pipeline
await session._review_event.wait()   # 挂起, 等 set()

# 协程B: review_session_candidate (HTTP handler)
session._review_event.set()          # 唤醒协程A
```

这全部发生在**同一个线程**上。Event 不涉及线程同步，只是协程调度。

### `notify()` + SSE = 实时推送

```python
# PipelineSession.notify():
def notify(self, phase, **data):
    self.phase = phase                     # 更新状态
    self._update_event.set()               # 唤醒 SSE 协程
    self._update_event = asyncio.Event()   # 新建 Event 给下次用

# SSE 协程 (pipeline_events):
async def event_stream():
    yield f"data: {json.dumps(session.to_status_dict())}\n\n"  # 立即发当前状态
    while True:
        await asyncio.wait_for(session.wait_for_update(), timeout=30)
        yield f"data: {json.dumps(session.to_status_dict())}\n\n"
        # ^^^^^ yield 挂起, 等下一次 notify
```

流程：`run_pipeline` 调用 `notify()` → `_update_event.set()` → SSE 协程被唤醒 → yield 新状态给浏览器 → 再次 `await wait_for_update()` 挂起。

### `call_llm` + Semaphore = 并发控制

```python
llm_semaphore = asyncio.Semaphore(2)  # 最多2个并发LLM调用

async def call_llm(backend, messages, **kwargs):
    async with llm_semaphore:          # 第3个调用会在这里挂起等待
        return await asyncio.to_thread(backend.chat, ...)
```

当两个 session 同时跑 pipeline 时：

```
Session A: await call_llm(...)  → 获取信号量, 提交到线程池 worker
Session B: await call_llm(...)  → 获取信号量, 提交到线程池 worker
Session A: await call_llm(...)  → 信号量满(2/2), 协程挂起等待
                                  (B的LLM完成后信号量释放, A自动唤醒)
```

Ollama 本地模型通常只能处理 1-2 个并发请求，信号量防止过载。

## SQLite 跨线程问题

### 问题

```python
# 事件循环线程 (thread 38200):
db = Database("data.db")
db.connect()                    # sqlite3.connect() 在 thread 38200

# 线程池 worker (thread 38872):
await to_thread(db.execute, "SELECT ...")   # 💥 不同线程用了 38200 的连接
```

SQLite 默认 `check_same_thread=True`，禁止跨线程使用连接。

### 解决方案

```python
db._conn = sqlite3.connect(db.connection_string, check_same_thread=False)
```

### 为什么安全

```
事件循环线程:  db = connect()
               │
               ├── await to_thread(db.execute("SELECT..."))  → worker A
               │   (await 等 worker A 完成才继续下一行)
               │
               ├── await to_thread(db.execute("INSERT..."))  → worker B
               │   (A 已经完成了, B 是新分配的 worker)
               │
               └── 永远不会有两个 worker 同时操作同一个 db 连接
```

`await` 的顺序性保证了同一时刻只有一个线程在操作连接。SQLite 的线程限制是防止并发写入导致数据损坏，而我们的使用模式不存在并发。

## 多 Session 并发场景

```
Session A (pipeline)              事件循环线程              Session B (pipeline)
  │                                   │                        │
  │ await to_thread(LLM_call_1)       │                        │
  │ ──────────────> 线程池            │                        │
  │                                   │  await to_thread(LLM_call_2)
  │                                   │ <────────────── 线程池  │
  │ (两个LLM调用同时在线程池执行)       │                        │
  │                                   │                        │
  │ LLM_1 返回                        │                        │
  │ session_a.notify(scoring)         │                        │
  │    → SSE_A 推送                   │                        │
  │                                   │  LLM_2 返回            │
  │                                   │  session_b.notify(search)
  │                                   │     → SSE_B 推送       │
```

每个 Session 有独立的：
- `PipelineSession` 实例（含独立的 `asyncio.Event`）
- SSE 连接
- DB 连接（`check_same_thread=False`）

共享的：
- 事件循环线程（通过 `await` 交替执行，互不阻塞）
- 线程池 workers（`to_thread` 按需分配）
- `llm_semaphore`（限制总 LLM 并发数）
