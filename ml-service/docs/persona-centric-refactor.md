# 以人设为中心的架构重构 — v2

## 1. 问题陈述

### 1.1 原始问题（v1 文档中提出，依然成立）

人设（Persona）是在以功能为先的流水线末端才被硬加上去的。
策略生成、评分、搜索、市场分析全部以全局方式运行——
人设仅在文案生成阶段（Phase 5）起作用。

### 1.2 M1-M3 完成后的现状

M1-M3 交付了一个可运行的 dry-run，但架构仍然是**全局优先**的：

```
bootstrap.py  →  全局 `strategies` 表（9 条策略，无 persona_id）
                      ↓
SarcasticAI.run()  →  db.list_strategies()  ← 读取全局表
                      ↓
                  StrategyGenerationSkill("strategy_generation")  ← 全局技能名
                      ↓
                  全局 DEFAULT_YOUTUBE_PRINCIPLES  ← 无人设感知
                      ↓
                  db.get_latest_run_yields()  ← 无人设过滤
```

五个具体问题：

| # | 问题 | 影响 |
|---|------|------|
| 1 | `strategies` 表没有 `persona_id` | 所有人设看到相同的 9 条策略 |
| 2 | 技能 DB 键为 `"strategy_generation"`（全局） | 人设 A 的反思会覆盖人设 B 的原则 |
| 3 | `strategy_generation.py` 中的 `DEFAULT_YOUTUBE_PRINCIPLES` | 不受人设的 `SEARCH_IDENTITY` 或 `CONTENT_AFFINITY` 影响 |
| 4 | `bootstrap.py` 是集中式的 | 硬编码 9 条策略 + 初始化全局技能 |
| 5 | `get_latest_run_yields()` / `get_strategy_yield_stats()` 查询全局数据 | 反思阶段存在跨人设数据污染 |

### 1.3 更深层的问题：顶层模块过多

```
app/
├── scoring/          ← 仅 heuristic.py，工具函数
├── discovery/        ← models.py + strategies.py + topic_generator.py
├── search/           ← 仅 aggregator.py，工具函数
├── outcomes/         ← 仅 tracker.py，工具函数
├── skills/           ← 自进化技能框架（保留）
├── bootstrap.py      ← 全局引导（应改为按人设）
├── tags.py           ← 与 _shared/tags.py 重复
├── description.py    ← 已删除
├── web_rag/          ← 已删除
├── prediction/       ← 已删除
├── embeddings/       ← 已删除
├── collectors/       ← 已删除（移至 deprecated/）
```

这些顶层模块要么是：
- **工具函数**，应归入 `_shared/`（scoring、aggregator、outcomes、topic_generator）
- **按人设的数据**，应放入各人设目录内（strategies、bootstrap）
- **已被删除**（web_rag、prediction、embeddings、collectors、description）

---

## 2. 设计目标

> 每个人设的完整流水线是独立的。
> 唯一共享的基础设施是：数据库适配器、LLM 后端、无状态工具函数。
> 没有全局策略，没有全局技能，没有全局原则，没有全局引导。

### 2.1 共享部分（`_shared/` 中的无状态工具）

这些是**纯函数**——接收参数、返回结果、不持有任何人设状态：

| 函数 | 来源 |
|------|------|
| `search_youtube_videos()` | YouTube Data API 封装 |
| `search_bilibili()` | Bilibili API 封装 |
| `check_transportability()` | 内容安全 + LLM 判断 |
| `submit_upload()`, `get_uploaded_ids()` | Go 服务 HTTP 客户端 |
| `generate_tags()`, `fetch_video_tags()` | Bilibili 标签聚合 |
| `heuristic_score()`, `ScoringParams` | 评分计算（无 DB 状态） |
| `SearchAggregator`, `SearchCandidate` | 去重 + 排序容器 |
| `OutcomeTracker` | 反馈循环记录辅助工具 |
| `TopicGenerator` | 基于 LLM 的查询生成/优化 |
| `YouTubeCandidate` | 数据模型 |
| `create_backend()` | LLM 后端工厂函数 |

### 2.2 按人设的部分（位于 `personas/sarcastic_ai/` 内）

| 关注点 | 拥有内容 |
|--------|----------|
| **策略** | 使用哪 9（或 5、或 20）条策略，带有人设定制的描述 |
| **策略引导** | 将自身策略以 `persona_id` 写入数据库 |
| **技能实例** | `StrategyGenerationSkill(name="sarcastic_ai::strategy_generation")` |
| **原则** | 独立进化的 `youtube_principles` 和 `bilibili_principles` |
| **评分参数** | 带有人设定制类目加分/阈值的 `ScoringParams` |
| **内容偏好** | 类目权重（游戏 0.9、教育 0.6 等） |
| **搜索身份** | 注入到策略生成的 LLM 提示词中 |
| **文案生成** | 系统提示词、few-shot 示例、风格词库 |
| **流水线阶段** | 运行哪些阶段、以何种顺序、使用什么参数 |
| **反思** | 按 `persona_id` 过滤的产出反思和结果反思 |

### 2.3 共享但按人设命名空间隔离的部分（数据库）

| 表 | 命名空间键 |
|----|-----------|
| `strategies` | `persona_id` 列（每个人设初始化自己的） |
| `skills` / `skill_versions` | 技能名 = `"{persona_id}::strategy_generation"` |
| `strategy_runs` | 已有 `persona_id` |
| `scoring_params` | `persona_id` 列 |
| `review_decisions` | 已有 `persona_id` |
| `persona_config` | 已有 `persona_id` |

---

## 3. 目标目录结构

```
app/
├── personas/
│   ├── __init__.py                    # ALL_PERSONAS 注册表，PersonaOrchestrator
│   ├── protocol.py                    # Persona Protocol, RunContext, RunResult
│   ├── _shared/                       # 无状态工具函数
│   │   ├── __init__.py
│   │   ├── youtube.py                 # search_youtube_videos(), YouTubeCandidate
│   │   ├── bilibili.py               # search_bilibili(), BilibiliSearchResult
│   │   ├── transportability.py        # check_transportability() + 内容安全
│   │   ├── upload.py                  # submit_upload(), get_uploaded_ids()
│   │   ├── tags.py                    # generate_tags(), fetch_video_tags()
│   │   ├── scoring.py                 # ScoringParams, heuristic_score()
│   │   ├── aggregator.py             # SearchAggregator, SearchCandidate
│   │   ├── outcomes.py               # OutcomeTracker
│   │   ├── topic_generator.py        # TopicGenerator（LLM 查询生成/优化）
│   │   ├── search_loop.py            # agentic_search_loop() [未来]
│   │   ├── review.py                 # interactive_review() — 交互式人工审核 [M3]
│   │   ├── historian.py              # PersonaHistorian [未来，见 m4 文档]
│   │   └── llm.py                    # create_backend() 再导出
│   │
│   └── sarcastic_ai/                  # 傲娇AI — 完全自包含的人设
│       ├── __init__.py                # class SarcasticAI — 完整 7 阶段流水线
│       ├── strategies.py              # 该人设的策略定义 + 引导
│       ├── prompts.py                 # SYSTEM_PROMPT, STRATEGY_HINTS, few-shot 示例
│       ├── style.py                   # TsundereLexicon
│       └── config.py                  # PERSONA_ID, SEARCH_IDENTITY, CONTENT_AFFINITY
│
├── skills/                            # 自进化技能框架（不变）
│   ├── __init__.py
│   ├── base.py                        # Skill 基类
│   ├── strategy_generation.py         # StrategyGenerationSkill
│   └── market_analysis.py             # MarketAnalysisSkill
│
├── llm/
│   └── backend.py                     # LLMBackend Protocol + Ollama/Cloud 实现
│
├── db/
│   └── database.py                    # SQLite 适配器（strategies、scoring_params 加入 persona_id）
│
├── config.py                          # YOUTUBE_API_KEY 等
└── cli.py                             # CLI 入口
```

### 需要删除的模块（合并到 `_shared/` 或人设目录中）：

| 当前模块 | 目标位置 | 迁移内容 |
|----------|----------|----------|
| `app/scoring/__init__.py` | 删除 | — |
| `app/scoring/heuristic.py` | `_shared/scoring.py` | `ScoringParams`, `heuristic_score()` |
| `app/scoring/heuristic.py:bootstrap_scoring_params()` | `sarcastic_ai/strategies.py` | 按人设的引导逻辑 |
| `app/discovery/__init__.py` | 删除 | — |
| `app/discovery/models.py:YouTubeCandidate` | `_shared/youtube.py` | 已在该处导入 |
| `app/discovery/models.py:Recommendation` | 删除 | 人设架构下不再使用 |
| `app/discovery/strategies.py`（全局 9 条策略） | `sarcastic_ai/strategies.py` | 按人设的策略定义 |
| `app/discovery/strategies.py`（饱和度检查） | `_shared/bilibili.py` | 工具函数 |
| `app/discovery/topic_generator.py` | `_shared/topic_generator.py` | 共享工具 |
| `app/search/__init__.py` | 删除 | — |
| `app/search/aggregator.py` | `_shared/aggregator.py` | 工具类 |
| `app/outcomes/__init__.py` | 删除 | — |
| `app/outcomes/tracker.py` | `_shared/outcomes.py` | 工具类 |
| `app/bootstrap.py` | 删除 | 每个人设自行引导 |
| `app/tags.py` | 删除（已在 `_shared/tags.py` 中） | — |

---

## 4. 关键架构变更

### 4.1 策略按人设隔离

**变更前：**
```python
# bootstrap.py — 全局
INITIAL_STRATEGIES = [{"name": "gaming_deep_dive", ...}, ...]  # 9 条策略
db.add_strategy(name=..., ...)  # 无 persona_id
```

**变更后：**
```python
# personas/sarcastic_ai/strategies.py
SARCASTIC_AI_STRATEGIES = [
    {
        "name": "gaming_deep_dive",
        "description": "In-depth game reviews... opinionated content with strong takes. "
                       "The AI persona thrives on drama, broken launches, corporate greed.",
        # ↑ 人设定制的描述
        ...
    },
    ...
]

def bootstrap_strategies(db: Database, persona_id: str) -> int:
    """初始化该人设的策略。幂等操作。"""
    count = 0
    for s in SARCASTIC_AI_STRATEGIES:
        existing = db.get_strategy(s["name"], persona_id=persona_id)
        if existing:
            continue
        db.add_strategy(..., persona_id=persona_id)
        count += 1
    return count
```

**数据库变更：**
```sql
ALTER TABLE strategies ADD COLUMN persona_id TEXT DEFAULT NULL;
-- 查询变为: WHERE persona_id = ? AND is_active = 1
```

### 4.2 技能按人设隔离（命名空间）

**变更前：**
```python
strategy_skill = StrategyGenerationSkill(db=db, backend=backend)
# DB 键: "strategy_generation" — 全局，所有人设共享
```

**变更后：**
```python
strategy_skill = StrategyGenerationSkill(
    name=f"{self.persona_id}::strategy_generation",
    db=db, backend=backend,
)
# DB 键: "sarcastic_ai::strategy_generation" — 按人设隔离
```

每个人设的技能实例拥有独立的：
- `system_prompt`（注入了该人设的搜索身份）
- `youtube_principles`（仅由该人设的反思驱动进化）
- `bilibili_principles`（仅由该人设的结果反馈驱动进化）
- 版本历史（独立回滚）

### 4.3 人设将身份注入策略生成

**变更前：**
```python
# strategy_generation.py — 全局默认值，无人设感知
DEFAULT_YOUTUBE_PRINCIPLES = "- Specific queries outperform generic ones\n..."
```

**变更后：**
```python
# sarcastic_ai/__init__.py
from .config import SEARCH_IDENTITY, CONTENT_AFFINITY

# 将人设上下文注入策略生成
strategy_skill = StrategyGenerationSkill(
    name=f"{self.persona_id}::strategy_generation",
    db=db, backend=backend,
)
# 首次运行时用人设感知的原则覆盖默认值
if not strategy_skill._loaded_from_db:
    strategy_skill.youtube_principles = PERSONA_YOUTUBE_PRINCIPLES
    strategy_skill.bilibili_principles = PERSONA_BILIBILI_PRINCIPLES

gen_result = strategy_skill.execute({
    "strategies_with_full_context": strategies_context,
    "recent_outcomes_with_youtube_context": recent_outcomes,
    "hot_words": "(not available)",
    "persona_identity": SEARCH_IDENTITY,  # 新增：注入到提示词中
})
```

`_default_system_prompt()` 新增 `{persona_identity}` 占位符：
```python
def _default_system_prompt(self) -> str:
    return (
        "You are an expert at finding YouTube videos for transport to Bilibili.\n\n"
        "{persona_identity}\n\n"  # 新增
        "YouTube search principles:\n{youtube_principles}\n\n"
        "Bilibili audience principles:\n{bilibili_principles}\n\n"
        "Respond in the exact JSON format requested."
    )
```

### 4.4 评分参数按人设隔离

**变更前：**
```python
params_row = db.get_scoring_params()  # 全局
```

**变更后：**
```python
params_row = db.get_scoring_params(persona_id=self.persona_id)
if not params_row:
    # 首次运行：使用人设默认值
    scoring_params = ScoringParams(
        youtube_min_views=10_000,
        category_bonuses={20: 1.5, 28: 1.3, 24: 1.2},  # 人设定制
    )
    db.save_scoring_params(scoring_params.to_json(), persona_id=self.persona_id)
```

### 4.5 反思/结果反馈按人设隔离

**变更前：**
```python
yield_data = db.get_latest_run_yields(limit=50)  # 所有人设混在一起
strategy_stats = db.get_strategy_yield_stats()     # 所有人设混在一起
```

**变更后：**
```python
yield_data = db.get_latest_run_yields(persona_id=self.persona_id, limit=50)
strategy_stats = db.get_strategy_yield_stats(persona_id=self.persona_id)
```

### 4.6 引导按人设隔离

**变更前：**
```python
# real_run.py
from app.bootstrap import run_bootstrap
run_bootstrap(db, skip_llm=True)  # 全局：初始化 9 条策略、全局技能
```

**变更后：**
```python
# 每个人设在 run() 内部自行引导
class SarcasticAI:
    async def run(self, db, context):
        from .strategies import bootstrap_strategies, bootstrap_scoring
        bootstrap_strategies(db, self.persona_id)  # 幂等
        bootstrap_scoring(db, self.persona_id)      # 幂等

        strategies = db.list_strategies(persona_id=self.persona_id)
        ...
```

不再有全局的 `bootstrap.py`。每个人设完全自包含。

---

## 5. 共享工具函数（`_shared/`）设计

### 5.1 原则

所有 `_shared/` 函数都是**无状态工具**：
- 接收参数，返回结果
- 不持有数据库状态、人设配置或 LLM 提示词记忆
- 人设通过函数参数和回调注入其身份

### 5.2 需要新增的函数

```python
# _shared/scoring.py（从 app/scoring/heuristic.py 迁移）
@dataclass
class ScoringParams:
    youtube_min_views: int = 10_000
    ...

def heuristic_score(views, likes, duration, category_id, opportunity, params) -> float: ...

# _shared/aggregator.py（从 app/search/aggregator.py 迁移）
@dataclass
class SearchCandidate: ...
class SearchAggregator: ...

# _shared/outcomes.py（从 app/outcomes/tracker.py 迁移）
class OutcomeTracker: ...

# _shared/topic_generator.py（从 app/discovery/topic_generator.py 迁移）
class TopicGenerator: ...
```

### 5.3 已存在于 `_shared/` 中的函数

- `youtube.py` — `search_youtube_videos()`, `YouTubeCandidate`
- `bilibili.py` — `search_bilibili()`, `BilibiliSearchResult`
- `transportability.py` — `check_transportability()` + 硬安全过滤
- `upload.py` — `submit_upload()`, `get_uploaded_ids()`
- `tags.py` — `generate_tags()`, `fetch_video_tags()`
- `llm.py` — `create_backend()` 再导出

### 5.4 M3 共享函数：交互式审核

```python
# _shared/review.py — 交互式人工审核（无状态工具）
@dataclass
class ReviewDecision:
    video_id: str
    decision: str           # "approved" | "rejected" | "revised"
    final_title: str
    final_desc: str
    final_tsundere: int
    reject_reason: str
    feedback_rounds: list[dict]
    review_time_seconds: float

def interactive_review(
    jobs: list[dict],
    regenerate_fn: Callable,    # 人设提供的重新生成回调
    persona_id: str,
    db: Database,
    max_revisions: int = 3,
) -> list[dict]:
    """逐个展示候选视频，用户选择通过/修改/拒绝。
    返回通过的 jobs（标题/简介可能已被用户要求修改）。"""
    ...
```

### 5.5 未来的共享函数

```python
# _shared/search_loop.py — 智能体式多轮搜索
async def agentic_search_loop(
    queries, searcher, evaluator, refiner,  # refiner = 人设回调
    already_seen, persona_id, db,
    max_rounds=3, quota_budget=2000,
) -> list[SearchCandidate]: ...

# _shared/historian.py — 跨运行分析（见 m4-historian-design.md）
class PersonaHistorian: ...
```

---

## 6. 数据库 Schema 变更

### 6.1 `strategies` 表

```sql
-- 新增 persona_id 列
ALTER TABLE strategies ADD COLUMN persona_id TEXT DEFAULT NULL;

-- 按人设查询的索引
CREATE INDEX IF NOT EXISTS idx_strategies_persona ON strategies(persona_id, is_active);
```

所有 `db.list_strategies()`、`db.get_strategy()`、`db.add_strategy()` 方法新增可选的 `persona_id` 参数。NULL 表示遗留/无归属数据。

### 6.2 `scoring_params` 表

```sql
-- 新增 persona_id 列
ALTER TABLE scoring_params ADD COLUMN persona_id TEXT DEFAULT NULL;
```

`db.get_scoring_params(persona_id=...)` 和 `db.save_scoring_params(..., persona_id=...)`。

### 6.3 查询过滤

所有产出/统计查询新增 `WHERE persona_id = ?`：

```python
def get_latest_run_yields(self, persona_id: str = None, limit=50):
    query = "SELECT ... FROM strategy_runs sr JOIN strategies s ..."
    if persona_id:
        query += " WHERE sr.persona_id = ?"
        params = (persona_id,)
    ...

def get_strategy_yield_stats(self, persona_id: str = None):
    # 同样的模式
    ...
```

---

## 7. SarcasticAI 人设 — 完整所有权

### 7.1 `sarcastic_ai/strategies.py`（新建）

包含内容：
- `SARCASTIC_AI_STRATEGIES` — 9 条带有人设定制描述的策略定义
- `PERSONA_YOUTUBE_PRINCIPLES` — 反映该人设搜索风格的初始原则
- `PERSONA_BILIBILI_PRINCIPLES` — 反映该人设受众模型的初始原则
- `PERSONA_SCORING_PARAMS` — 带有人设定制类目加分的默认 `ScoringParams`
- `bootstrap_strategies(db, persona_id)` — 初始化策略
- `bootstrap_scoring(db, persona_id)` — 初始化评分参数

### 7.2 `sarcastic_ai/__init__.py` — 流水线变更

```python
class SarcasticAI:
    async def run(self, db, context):
        # 自引导（幂等）
        from .strategies import bootstrap_strategies, bootstrap_scoring
        bootstrap_strategies(db, self.persona_id)
        bootstrap_scoring(db, self.persona_id)

        # 阶段 1：策略生成 — 按人设命名空间的技能
        strategy_skill = StrategyGenerationSkill(
            name=f"{self.persona_id}::strategy_generation",
            db=db, backend=backend,
        )
        strategies = db.list_strategies(persona_id=self.persona_id)
        ...

        # 阶段 3：YouTube 搜索 — 使用 _shared 函数
        from app.personas._shared.youtube import search_youtube_videos
        from app.personas._shared.scoring import ScoringParams, heuristic_score
        from app.personas._shared.aggregator import SearchAggregator
        ...

        # 产出反思 — 按 persona_id 过滤
        yield_data = db.get_latest_run_yields(persona_id=self.persona_id)
        strategy_stats = db.get_strategy_yield_stats(persona_id=self.persona_id)
        ...
```

---

## 8. 实施里程碑

### M1：将工具代码迁移到 `_shared/` + 数据库 Schema 变更

**目标**：将所有工具代码整合到 `_shared/`，更新数据库以支持人设命名空间。

1. 迁移 `app/scoring/heuristic.py` → `_shared/scoring.py`
   - 保留 `ScoringParams`、`heuristic_score()`
   - 将 `bootstrap_scoring_params()` 迁移到人设级代码
2. 迁移 `app/search/aggregator.py` → `_shared/aggregator.py`
3. 迁移 `app/outcomes/tracker.py` → `_shared/outcomes.py`
4. 迁移 `app/discovery/topic_generator.py` → `_shared/topic_generator.py`
5. 将 `app/discovery/models.py:YouTubeCandidate` 迁入 `_shared/youtube.py`
6. 将 `app/discovery/strategies.py` 中的饱和度检查函数迁移到 `_shared/bilibili.py`
7. 数据库：为 `strategies`、`scoring_params` 表添加 `persona_id`
8. 数据库：为 `list_strategies()`、`get_strategy()`、`get_latest_run_yields()`、`get_strategy_yield_stats()`、`get_scoring_params()` 添加 `persona_id` 过滤
9. 删除空的顶层模块：`app/scoring/`、`app/search/`、`app/outcomes/`、`app/discovery/`
10. 删除 `app/bootstrap.py`、`app/tags.py`
11. 更新所有导入路径
12. **验证**：所有测试通过（同时更新测试中的导入路径）

### M2：按人设的策略 + 技能

**目标**：SarcasticAI 拥有其策略和技能实例的所有权。

1. 创建 `sarcastic_ai/strategies.py`，包含：
   - `SARCASTIC_AI_STRATEGIES`（9 条策略，人设定制描述）
   - `PERSONA_YOUTUBE_PRINCIPLES`、`PERSONA_BILIBILI_PRINCIPLES`
   - `PERSONA_SCORING_PARAMS`
   - `bootstrap_strategies(db, persona_id)`
   - `bootstrap_scoring(db, persona_id)`
2. 更新 `SarcasticAI.run()`：
   - 自引导策略和评分参数
   - 使用 `f"{self.persona_id}::strategy_generation"` 作为技能名
   - 使用 `persona_id=self.persona_id` 查询策略
   - 使用 `persona_id=self.persona_id` 查询产出/统计数据
   - 将 `SEARCH_IDENTITY` 注入策略生成提示词
3. 更新 `StrategyGenerationSkill`：
   - 在 `_default_system_prompt()` 中添加 `{persona_identity}` 占位符
   - 在 `execute()` 上下文中接收人设身份
4. 从 `real_run.py` 中移除全局 `bootstrap.py` 调用
5. **验证**：`real_run.py --no-upload --no-review --quota-budget 500` 正常运行

### M3：交互式人工审核

**目标**：在 Phase 5（文案生成）和 Phase 7（上传）之间插入真正的人工审核环节，支持通过、要求修改（多轮）、拒绝三种操作。

#### 设计原则

审核逻辑放在 `_shared/review.py`，是**无状态工具函数**——接收候选列表和一个人设提供的重新生成回调，返回审核决定列表。人设控制：
- 重新生成用什么 LLM、什么提示词、什么温度
- 最终哪些字段可以被修改

编排器（Orchestrator）和人设协议（Protocol）不需要任何变更。审核是人设流水线内部的一个阶段，不暴露到外部接口。

#### `_shared/review.py` 接口

```python
@dataclass
class ReviewDecision:
    video_id: str
    decision: str           # "approved" | "rejected" | "revised"
    final_title: str
    final_desc: str
    final_tsundere: int
    reject_reason: str
    feedback_rounds: list[dict]   # [{"feedback": "...", "title": "...", "desc": "...", "tsundere": N}, ...]
    review_time_seconds: float

def interactive_review(
    jobs: list[dict],           # Phase 5 输出的 upload_jobs
    regenerate_fn: Callable,    # 人设提供的重新生成回调
    persona_id: str,
    db: Database,
    max_revisions: int = 3,     # 单个视频最多修改轮数
) -> list[dict]:
    """交互式逐个审核，返回通过的 jobs（可能标题/简介已被修改）。"""
    ...
```

#### 交互流程

对每个候选视频，终端展示：

```
──────────────────────────────────────
  #1/3  [gaming_deep_dive]
  原标题: Starfield Was A Complete Mess — 200 Hours Later
  频道: AngryJoeShow  |  播放: 3,800,000  |  时长: 45m12s
  ──────────────────────────────────────
  B站标题: 人类花200小时才发现这游戏是垃圾 本AI看封面就知道了
  B站简介: 才不是因为好奇才看完的...
  傲娇指数: 9/10

  [y] 通过  [e] 要求修改  [n] 拒绝  [q] 跳过剩余全部拒绝
  >
```

用户输入：
- `y` — 通过，保留当前标题/简介，进入上传队列
- `e` — 要求修改，提示输入反馈（如"标题太长"、"傲娇感不够"），LLM 根据反馈重新生成，再次展示
- `n` — 拒绝，记录原因（可选输入），从上传队列移除
- `q` — 跳过剩余，全部标记为 rejected

修改轮次上限 `max_revisions=3`，超过后强制选择通过或拒绝。

#### `regenerate_fn` 回调签名

```python
def regenerate_fn(
    job: dict,              # 包含 candidate、strategy 等上下文
    feedback: str,          # 用户的修改意见
    previous_title: str,    # 上一轮生成的标题
    previous_desc: str,     # 上一轮生成的简介
) -> tuple[str, str, int]: # (new_title, new_desc, new_tsundere)
```

由人设在 `__init__.py` 中实现，使用自己的 SYSTEM_PROMPT + few-shot + 用户反馈：

```python
# sarcastic_ai/__init__.py
def _make_regenerate_fn(self, backend):
    def regenerate(job, feedback, prev_title, prev_desc):
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"原标题：{job['candidate'].title}\n..."},
            {"role": "assistant", "content": f"标题：{prev_title}\n简介：{prev_desc}"},
            {"role": "user", "content": f"修改意见：{feedback}\n请根据意见重新生成。"},
        ]
        response = backend.chat(messages=messages, temperature=1.0)
        return _parse_copy_response(response)
    return regenerate
```

#### DB 记录

每个审核决定写入 `review_decisions` 表：

```python
db.save_review_decision(
    persona_id=persona_id,
    strategy_run_id=None,
    youtube_video_id=job["video_id"],
    strategy_name=job["strategy"],
    decision="approved" | "rejected" | "revised",
    original_title=job["candidate"].title,
    original_desc="",
    final_title=final_title,
    final_desc=final_desc,
    feedback_rounds_json=json.dumps(rounds, ensure_ascii=False),
    reject_reason=reason,
    review_time_seconds=elapsed,
)
```

**注意**：当前代码在 Phase 5 就写了 `decision="approved"`，M3 需要将这个 `save_review_decision` 调用从 Phase 5 移到 Phase 6 审核完成后。

#### 与现有流水线的集成点

Phase 5（文案生成）输出 `upload_jobs`，Phase 5b（排序选取 top 3）后：

```python
# ── Phase 6: Human Review ──────────────────────────
if context.no_review:
    logger.info("Skipping review (--no-review)")
else:
    from app.personas._shared.review import interactive_review
    regenerate = self._make_regenerate_fn(backend)
    upload_jobs = interactive_review(
        jobs=upload_jobs,
        regenerate_fn=regenerate,
        persona_id=self.persona_id,
        db=db,
    )

# Phase 7 只上传 Phase 6 通过的 jobs
```

#### CLI 参数（已有，无需新增）

- `--no-review` — 跳过审核，全部自动通过（自动化运行）
- 默认行为 — 进入交互式审核

#### 终端编码

审核界面需要输出中文，Windows 上需要 UTF-8 输出。使用与 Phase 5 打印相同的 `io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")` 方式。输入端使用 `input()` + `PYTHONUTF8=1` 环境变量。

#### 测试策略

- 单元测试：mock `input()` + mock `regenerate_fn`，验证 approve/reject/revise 三条路径
- 集成测试：在 `--dry-run` 模式下手动验证交互体验

### M4：历史分析器 [未来]

- `_shared/historian.py` — 见 `docs/m4-historian-design.md`
- 按人设的分析，使用 `persona_id` 过滤
- 已完成设计，等待真实结果数据

---

## 9. 数据所有权边界

### 9.1 原则

> Python DB 只存与模型进化直接相关的数据。
> B站视频/评论/播放量等运营数据由 Go 后端维护，以 `persona_id` 为 key。
> Python 需要 B站结果数据时，HTTP 请求 Go 后端获取。

### 9.2 Go 后端现状

Go DB（`metadata.db`）目前有两张表：

**`uploads` 表**（已完成的上传记录）：
| 列 | 说明 |
|----|------|
| video_id (PK) | YouTube 视频 ID |
| channel_id | YouTube 频道 ID |
| bilibili_bvid | B站 BV号（上传成功后写入） |
| uploaded_at | 上传时间 |

**`upload_jobs` 表**（异步任务队列）：
| 列 | 说明 |
|----|------|
| id, video_id | 任务标识 |
| status | pending→downloading→uploading→completed/failed |
| title, description, tags | Python 提交的文案 |
| bilibili_bvid | 上传成功后解析 biliup 输出获得 |
| download_files | JSON 数组，下载的文件路径 |
| subtitle_status | 字幕生成状态 |
| error_message | 失败原因 |
| created_at, updated_at | 时间戳 |

**Go 端目前缺失的：**
- ❌ `persona_id` — 不知道是哪个人设提交的
- ❌ B站播放量/评论 — 上传后不追踪
- ❌ 策略名 — 不知道视频来自哪个搜索策略
- ❌ outcomes API — 没有供 Python 查询结果的端点

**现有 HTTP 端点：**
| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/upload` | 提交上传任务（202 异步） |
| GET | `/upload/status?id=X` | 查询单个任务状态 |
| GET | `/upload/jobs?limit=N` | 列出最近任务 |
| GET | `/upload/uploaded-ids` | 获取所有已上传的 video_id |

### 9.3 Go 后端需要的变更（M4 前置）

**Phase 1：添加 persona_id + strategy 元数据**

```sql
-- upload_jobs 表新增列
ALTER TABLE upload_jobs ADD COLUMN persona_id TEXT DEFAULT '';
ALTER TABLE upload_jobs ADD COLUMN strategy_name TEXT DEFAULT '';

-- uploads 表新增列
ALTER TABLE uploads ADD COLUMN persona_id TEXT DEFAULT '';
```

Python 提交时传递：
```json
POST /upload
{
  "video_id": "xxx",
  "title": "...",
  "description": "...",
  "tags": "...",
  "persona_id": "sarcastic_ai",
  "strategy_name": "tech_teardown"
}
```

**Phase 2：B站数据追踪（M4 依赖）**

```sql
-- 新表：存储 B站视频指标
CREATE TABLE bilibili_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bilibili_bvid TEXT NOT NULL,
    video_id TEXT NOT NULL,
    persona_id TEXT DEFAULT '',
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

Go 后端新增定时任务：轮询 B站公开 API 更新已上传视频的播放量。

**Phase 3：Outcomes API**

```
GET /outcomes?persona_id=sarcastic_ai&days=14
    → [{bvid, video_id, title, persona_id, strategy_name,
        views, likes, comments, uploaded_at, checked_at}, ...]

GET /outcomes/stats?persona_id=sarcastic_ai
    → {total_uploaded, avg_views, by_strategy: [{name, count, avg_views}]}
```

### 9.3 Python DB 拥有的数据

| 表 | 用途 | 谁写 |
|---|---|---|
| `strategies` | 策略定义 + 累积效果统计 | bootstrap + 反思 |
| `strategy_runs` | 每次搜索的 query + 结果摘要 | Phase 3 |
| `scoring_params` | 评分阈值和类目加分 | bootstrap + Historian |
| `review_decisions` | 审核决定 + 完整修改链 | Phase 6 |
| `skills` / `skill_versions` | 技能系统提示词 + 版本历史 | 反思 |
| `persona_config` | 人设 KV 配置 | 人设自身 |

### 9.4 Loop 2 数据流（M4 实现时）

```
Go 后端轮询 B站 API → 更新 upload_jobs 表中的 bilibili_views/comments
                ↓
Python Historian HTTP GET /outcomes?persona_id=...
                ↓
reflect_on_outcomes() 分析什么内容在 B站 成功
                ↓
更新 bilibili_principles（存在 Python skill_versions 中）
更新 scoring_params（存在 Python DB 中）
```

### 9.5 strategy_runs 表中的 B站 字段

`bilibili_bvid`、`bilibili_views`、`outcome` 等列**保留但不由 Python 主动维护**。
Historian 运行时可从 Go 后端拉取数据临时写入用于分析，或直接在内存中关联。

---

## 10. 明确不做的事

- **不做全局引导** — 每个人设自行引导
- **不做全局策略** — 每个人设定义并进化自己的策略
- **不做共享技能实例** — 每个人设通过命名空间隔离其技能
- **不做共享原则** — 每个人设的 LLM 原则独立进化
- **不做统一的 PersonaConfig** — 每个人设定义自己的配置结构
- **不用 LLM 框架**（LangChain 等）— 自进化提示词不被任何框架支持
- **不将 ML 预测绑定到人设** — 预测模型是客观的，不受人设影响
- **不过早创建第二个人设** — 先用 SarcasticAI 验证架构
- **Python 不存 B站运营数据** — Go 后端是 B站数据的唯一来源，Python 通过 HTTP 获取

---

## 11. 人设协议（与 v1 相同，无变更）

```python
@runtime_checkable
class Persona(Protocol):
    @property
    def persona_id(self) -> str: ...

    async def run(self, db: Database, context: RunContext) -> RunResult: ...

    def apply_historian_update(
        self, db: Database, summary: PerformanceSummary,
    ) -> list[str]: ...
```

协议设计上刻意保持精简。内部流水线结构是人设自己的事。详见 v1 文档第 3 节的设计理由。

---

## 12. 对比示例：DashcamClips 人设

用一个截然不同的人设来验证架构的通用性：

```python
class DashcamClips:
    """3 步流水线。无策略生成，无文案，无 few-shot。"""

    persona_id = "dashcam_clips"

    async def run(self, db, context):
        # 无需引导 — 硬编码查询词，不用数据库策略
        queries = ["insane dashcam compilation 2026", "security camera caught", ...]

        # 步骤 1：直接搜索（无策略技能，无主题生成器）
        videos = []
        for q in queries:
            videos.extend(search_youtube_videos(q, max_results=20))

        # 步骤 2：过滤（播放量 > 100K，时长 < 20 分钟，去重）
        filtered = [v for v in videos if v.views > 100_000 and ...]

        # 步骤 3：上传，使用简单翻译的标题（无人设文案）
        for v in filtered:
            submit_upload(context.go_url, v.video_id, translate(v.title), "")

        return RunResult(persona_id=self.persona_id, ...)

    def apply_historian_update(self, db, summary):
        return []  # 无需学习
```

没有策略表，没有技能，没有原则，没有反思。
相同的 Protocol 接口，完全不同的内部实现。

## 13. 推荐算法视角审查（2026-03-30）

当前 pipeline 本质上是一个多阶段推荐系统：

```
Candidate Generation → Filtering → Scoring → Re-ranking → Human Review
     (Phase 1-3)        (Phase 4)   (Phase 3)  (Phase 5b)   (Phase 6)
```

### 13.1 已发现的结构性问题

#### 问题 1：双重打分，信号不一致

Phase 3 (heuristic_score) 和 Phase 5b (_rank_and_select) 用两套完全不同的打分函数：

| 信号 | Phase 3 权重 | Phase 5b 权重 | 问题 |
|------|-------------|--------------|------|
| views (log) | 0.2 | 0.3 | 重复计算，权重不同 |
| duration | 0.2 (sweet spot) | 0.2 (3档阶梯) | 两套不同逻辑 |
| likes/engagement | 0.3 | 不用 | Phase 3 看重但 5b 丢弃 |
| category | bonus × affinity (乘两次) | 仅用于 duration 豁免 | Phase 3 双重乘法 |
| opportunity_score | **0.3 (硬编码 0.5)** | 不用 | 30% 权重给了常数 |
| tsundere | 不存在 | **0.5** | LLM 自评占最大权重 |

**opportunity_score 是常量 0.5**——Phase 3 有 30% 的打分权重浪费在一个固定值上。

#### 问题 2：Tsundere 自评占 50% 排序权重

LLM 给自己的文案打傲娇指数，这个信号天然不稳定（同一文案修改后评分从 9 变 5）。
用它做最终排序的 50% 权重 = 让最不可靠的信号决定最终选品。

#### 问题 3：无 Explore/Exploit 机制

7 个策略的 query 分配完全靠 LLM 自由发挥 (T=0.9)。
没有 bandit (UCB/Thompson Sampling) 根据历史表现自动分配预算。
高 yield 策略和低 yield 策略拿到的 query 数量一样多。

#### 问题 4：Review 反馈没有闭环到 scoring

用户 reject 的视频不影响未来打分。反复被 reject 的内容类型，系统不会学到。
Loop 1 只看 YouTube 搜索 yield（搜到没有），不看 review 结果（人类喜不喜欢）。

#### 问题 5：Category bonus 双重乘法

Phase 3 中 `heuristic_score()` 内部已乘 `category_bonus`，外部又乘 `CONTENT_AFFINITY`：
- Gaming (cat 20): 1.5 × 0.9 = 1.35
- News (cat 25): 0.9 × 0.7 = 0.63
差距被不必要地放大。

#### 问题 6：LLM 模型能力瓶颈

当前使用 qwen2.5:7b (4GB)，对复杂指令遵循能力不足：
- 标题规则 5+ 条，7B 无法同时遵守
- 否定指令（"不要直接翻译"）遵守率低
- 修改 (revision) 时仍然直译
- 多轮对话中容易丢失人设

### 13.2 改进计划

#### 短期（不改架构）

- [ ] 删除 opportunity_score=0.5 常量，把 30% 权重重新分配
- [ ] Phase 5b 降低 tsundere 权重 (0.5→0.2)，增加 views + engagement
- [ ] 统一 Phase 3 和 5b 的 duration 逻辑
- [ ] 修复 category bonus 双重乘法（选一处应用）
- [ ] 评估升级 LLM 模型 (qwen2.5:14b 或 32b)

#### 中期（加反馈闭环）

- [ ] Review 决策 (approve/reject) 回流策略选择——被 reject 多的策略降权
- [ ] 用 review_decisions 表的 accept rate per strategy 做简单 explore/exploit
- [ ] 加 Loop 3：review 反馈 → 更新 copy generation 原则（不只是 few-shot）

#### 长期（正式 RecSys）

- [ ] Contextual bandit 分配策略预算（state = yield + review accept rate + 时间）
- [ ] Tsundere 自评替换为 reward model（用 review_decisions 训练预测 approve 概率）
- [ ] 多阶段打分统一为单一 scoring pipeline，避免信号冲突
