# Fast Prototype 工作报告

> 截至 2026-04-01，记录所有快速原型阶段实现的功能、已知问题、测试状态，
> 以及 formalize 时需要完成的工作。

---

## 1. 已实现功能总览

### 1.1 前端 Dashboard（React + Vite）

| 功能 | 文件 | 状态 | 测试方式 |
|------|------|------|----------|
| Pipeline 控制面板 | `ServiceControls.tsx` | 可用 | 手动：启动/停止 Go、触发 pipeline |
| Pipeline 进度条 | `PipelineProgress.tsx` | 可用 | 手动：观察步骤切换 |
| 视频审核（approve/reject/regenerate） | `VideoReview.tsx` | 可用 | 手动：审核候选视频 |
| 策略选择器 | `StrategyPicker.tsx` | 可用 | 手动：勾选策略确认 |
| 字幕管理（预览/重试/审批/删除） | `SubtitleManager.tsx` | 可用 | 手动：管理已上传视频字幕 |
| 弹幕生成（单个+批量） | `SubtitleManager.tsx` | 可用 | 手动：点击生成弹幕，等待黄色 banner |
| AI 进化监控 | `SkillEvolution.tsx` | 可用 | 手动：查看 skill、触发反思、审批 |
| 策略通过率表格 | `SkillEvolution.tsx` | 可用 | 需有 review 数据才能验证 |
| Vite 代理 | `vite.config.ts` | 可用 | `/api/go` → :8081, `/api/py` → :8000 |

**无自动化测试。** 前端全部靠手动验证。

### 1.2 AI 进化系统

| 功能 | 后端文件 | 状态 | 测试方式 |
|------|----------|------|----------|
| Skill 基类（版本化 prompt） | `skills/base.py` | 可用 | 638 个单元测试覆盖 |
| StrategyGenerationSkill | `skills/strategy_generation.py` | 可用 | 单元测试 |
| AnnotationSkill（弹幕自进化） | `skills/annotation.py` | 可用 | 手动触发 + CLI 测试 |
| Yield 反思（Loop 1） | `skills/strategy_generation.py` | 可用，需审批 | 手动：AI进化 tab 点按钮 |
| Outcome 反思（Loop 2） | `skills/strategy_generation.py` | 可用，需审批 | 手动：需先采集 B站数据 |
| 弹幕反思 | `skills/annotation.py` | 可用，需审批 | 手动：需先有弹幕编辑反馈 |
| 反思审批流程 | 前端 + `/skills/{name}/apply-reflection` | 可用 | 手动 |
| Review 通过率注入反思 | `server.py` + `strategy_generation.py` | 可用 | 手动 |
| Loop 2 数据采集 | `loop2_collector.py` | 部分可用 | B站 API 需 headers，已修复 412 |
| 版本历史 + 回滚 | `skills/base.py` + 前端 | 可用 | 手动 |

### 1.3 弹幕系统（Annotation）

| 功能 | 文件 | 状态 | 测试方式 |
|------|------|------|----------|
| CLI 模式弹幕生成 | `annotate_cli.py` | 可用 | Go 调用 subprocess |
| Go 调用 Python subprocess | `subtitle_pipeline.go` | 可用 | 手动：点生成弹幕 |
| 弹幕预览 | `SubtitleManager.tsx` | 可用 | 手动 |
| 生成等待指示器 | `SubtitleManager.tsx` | 可用 | 手动：黄色 banner + 轮询 |
| 软删除视频 | `store.go` + `http.go` | 可用 | 手动 |

### 1.4 Python 服务端 API（FastAPI）

| Endpoint | 用途 | 测试 |
|----------|------|------|
| `GET /skills` | 列出所有 skill | 手动 |
| `GET /skills/{name}/versions` | 版本历史 | 手动 |
| `POST /skills/reflect` | 触发反思（dry-run） | 手动 |
| `POST /skills/{name}/apply-reflection` | 审批反思 | 手动 |
| `POST /skills/{name}/rollback` | 回滚版本 | 手动 |
| `POST /skills/{name}/update` | 手动编辑 prompt | 手动 |
| `POST /loop2/collect` | 采集 B站数据 | 手动（412 已修） |
| `GET /loop2/stats` | Loop 2 统计 | 手动 |
| `GET /review-stats` | 策略通过率 | 手动 |
| `POST /annotation/feedback` | 弹幕编辑反馈 | 未测试 |

---

## 2. 已知问题 & 与设计文档的差距

### 2.1 架构偏差（vs persona-centric-refactor.md）

| # | 问题 | 影响 | Formalize 优先级 |
|---|------|------|-----------------|
| 1 | Skill DB 键是全局的（`strategy_generation`），文档要求 `sarcastic_ai::strategy_generation` | 加第二人设就冲突 | 中（单人设期间不影响） |
| 2 | 策略生成 prompt 没有 `{persona_identity}` 占位符 | 人设身份没注入搜索 | 中 |
| 3 | 旧模块未清理（`app/scoring/`、`app/search/`、`app/outcomes/`、`app/discovery/`） | 代码冗余，导入混乱 | 低（不影响运行） |
| 4 | ~~Go 端缺 `persona_id`、`strategy_name` 列~~ | ~~Loop 2 无法按策略分析~~ | ~~高~~ ✅ 已完成 |
| 5 | `opportunity_score` 常量 0.5 占 30% 打分权重 | 30% 打分浪费 | 高 |
| 6 | tsundere 自评占 Phase 5b 排序 50% 权重 | 不稳定信号决定选品 | 高 |
| 7 | category bonus 双重乘法 | 类目差距被不必要放大 | 中 |
| 8 | 无 Explore/Exploit 机制 | 低效策略仍获同等预算 | 低（数据不够） |

### 2.2 技术债

| 问题 | 描述 |
|------|------|
| Whisper 在非英语视频上崩溃 | `exit status 0xc0000409`，Windows stack buffer overrun |
| 前端零测试 | 全靠手动 |
| Python 新 API 零测试 | `/skills/*`, `/loop2/*`, `/review-stats` 没有自动化测试 |
| 弹幕编辑反馈未闭环 | `POST /annotation/feedback` 存了但前端没有编辑弹幕的 UI |
| ~~`subtitle-pipeline.md` 过时~~ | ~~已更新~~ |
| Loop 2 成功阈值硬编码 50000 | 应该可配置或按策略动态调整 |

### 2.3 未实现的文档功能

| 功能 | 文档 | 状态 |
|------|------|------|
| Historian（跨人设学习） | m4-historian-design.md | 未实现，等数据 |
| Go 端 B站 stats 轮询 | persona-centric-refactor.md §9.3 | 未实现，Python 直调 B站 API |
| Go 端 `/outcomes` API | persona-centric-refactor.md §9.3 | 未实现 |
| 多用户 Chat 架构 | architecture.md | 未实现，属未来规划 |
| DashcamClips 第二人设 | persona-centric-refactor.md §12 | 未实现 |

---

## 3. Formalize 计划

### Phase F1：打分系统修复（影响选品质量）

**目标**：修复推荐算法的结构性问题。

- [ ] 删除 `opportunity_score=0.5` 常量，重新分配 30% 权重
- [ ] Phase 5b 降低 tsundere 权重 (0.5→0.2)，增加 views + engagement
- [ ] 修复 category bonus 双重乘法
- [ ] 统一 Phase 3 和 5b 的 duration 逻辑

**测试**：
- 单元测试：给定候选视频集，验证排序结果的合理性
- 回归测试：跑一次 dry-run pipeline，对比修改前后的 top-3 选品
- 在 `test_scoring.py` 里增加 edge case（全高分、全低分、tsundere 极端值）

### Phase F2：Go 端元数据扩展 ✅

**目标**：Go 的 `upload_jobs` 加 `persona_id` + `strategy_name`，为 Loop 2 和 Historian 打基础。

- [x] Go 端 migration：`ALTER TABLE upload_jobs ADD COLUMN persona_id TEXT DEFAULT ''`
- [x] Go 端 migration：`ALTER TABLE upload_jobs ADD COLUMN strategy_name TEXT DEFAULT ''`
- [x] Python `submit_upload()` 传递 `persona_id` 和 `strategy_name`
- [x] Go `/upload/jobs` 返回新字段
- [x] Loop 2 collector 用 `strategy_name` 关联 `strategy_runs`

**测试**：
- Go 单元测试：插入带 persona_id 的 job，查询确认返回 ✅
- 回归：现有 Go 测试全部通过 ✅

### Phase F3：Skill 命名空间

**目标**：技能 DB 键改为 `{persona_id}::skill_name`。

- [ ] `StrategyGenerationSkill` 构造时传入 `name=f"{persona_id}::strategy_generation"`
- [ ] `AnnotationSkill` 同理
- [ ] 数据迁移：将现有 `strategy_generation` 记录重命名为 `sarcastic_ai::strategy_generation`
- [ ] 前端 API 适配新的 skill name 格式
- [ ] server.py 的 reflect/rollback/update 端点适配

**测试**：
- 单元测试：两个不同 persona_id 的技能实例互不干扰
- 迁移测试：旧数据迁移后版本历史完整
- 回归：现有 skill 相关测试全部通过

### Phase F4：弹幕编辑闭环

**目标**：用户在前端编辑弹幕（保留/删除/修改），反馈发回 Python 驱动 AnnotationSkill 进化。

- [ ] `SubtitleManager.tsx`：弹幕预览区加编辑模式（checkbox 保留/删除，文本可改）
- [ ] Go `subtitle-approve` 端点接受修改后的 annotations body
- [ ] 前端在 approve 时计算 diff，POST 到 `/annotation/feedback`
- [ ] AnnotationSkill 反思基于真实反馈数据

**测试**：
- 手动测试：编辑弹幕 → 审批 → 确认 feedback 存入 DB → 触发弹幕反思 → 验证 prompt 变更合理
- 自动化：Python 端 `test_annotation_skill.py` 用 mock feedback 验证反思流程

### Phase F5：架构迁移（Python 单入口 + SSE）✅

> 详见 `docs/python-orchestrator-refactor.md`

**目标**：前端统一通过 Python 入口，pipeline 改用 SSE 实时推送。

- [x] Python 加代理层：`/api/uploads/*` 代理到 Go `:8081`
- [x] 前端 `api/index.ts` 统一 `API_BASE = '/api'`
- [x] Vite 代理改为单入口（`/api` → Python :8000）
- [x] Python 加 SSE endpoint（`/api/pipeline/events`）
- [x] `_run_pipeline()` 从 threading 改为 asyncio
- [x] 消除 30 分钟超时
- [x] 前端 PipelineProgress 改用 EventSource
- [x] Review 阶段快照 + 启动时恢复

**测试**：
- 手动：pipeline 进度实时推送，策略选择和审核无超时
- Python 崩溃后重启，review 阶段自动恢复（自动化测试 ✅）

### Phase F6：前端测试 + API 测试

**目标**：关键路径有自动化测试覆盖。

- [ ] Python API 测试：用 `httpx` + `TestClient` 测试所有新 endpoint
  - `test_api_skills.py`：list/versions/reflect/apply/rollback/update
  - `test_api_loop2.py`：collect（mock B站 API）、stats
  - `test_api_review_stats.py`：review-stats 聚合正确性
- [ ] 前端 smoke test：Playwright 或 Cypress，覆盖三个 tab 的基本渲染

### Phase F7：文档更新

- [x] 更新 `subtitle-pipeline.md`（whisper_autosrt + annotate_cli + 人工审批）
- [x] 替换 `go-orchestrator-architecture.md` → `python-orchestrator-refactor.md`
- [ ] 更新 `persona-centric-refactor.md` 中标注已完成的 milestone
- [ ] 新增 `ai-evolution.md` 文档，描述反思审批流程

---

## 4. 运行环境

```
Go 服务:  :8081  --db-path metadata.db --ml-service-dir ml-service --llm-backend ollama
Python:   :8000  uvicorn app.server:app
前端:     :5173  npm run dev (Vite proxy → Go + Python, 迁移后统一 → Python)
LLM:      Ollama (qwen2.5:7b, localhost:11434)
```

---

## 5. 关键文件索引

```
frontend/
├── src/App.tsx                          # 三 tab 路由：Pipeline / 字幕管理 / AI进化
├── src/api/index.ts                     # 所有 API 调用
├── src/components/
│   ├── SkillEvolution.tsx               # AI 进化 tab（反思/审批/版本/Loop2/通过率）
│   ├── SubtitleManager.tsx              # 字幕+弹幕管理
│   ├── VideoReview.tsx                  # 候选视频审核
│   ├── ServiceControls.tsx              # Go/Pipeline 控制
│   ├── PipelineProgress.tsx             # Pipeline 进度
│   └── StrategyPicker.tsx               # 策略选择

ml-service/
├── app/server.py                        # FastAPI 主服务
├── app/skills/
│   ├── base.py                          # Skill 基类（版本化/回滚/反思）
│   ├── strategy_generation.py           # 策略生成技能（yield/outcome 反思）
│   └── annotation.py                    # 弹幕生成技能（feedback 反思）
├── app/loop2_collector.py               # B站数据采集
├── app/annotate_cli.py                  # 弹幕 CLI（Go subprocess 调用）
├── app/personas/sarcastic_ai/           # 傲娇AI 人设
│   ├── __init__.py                      # 7 阶段 pipeline
│   ├── strategies.py                    # 9 条策略定义
│   ├── prompts.py                       # System prompt + few-shot
│   └── config.py                        # PERSONA_ID, SEARCH_IDENTITY
├── app/personas/_shared/                # 共享工具函数
├── app/db/database.py                   # SQLite 适配器

internal/app/                            # Go 后端
├── http.go                              # HTTP 端点
├── store.go                             # SQLite 存储（upload_jobs）
├── queue.go                             # 异步任务队列
├── subtitle_pipeline.go                 # 字幕+弹幕 pipeline
└── subtitle.go                          # whisper_autosrt 调用
```
