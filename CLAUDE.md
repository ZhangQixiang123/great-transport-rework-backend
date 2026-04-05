# YT Transport — YouTube-to-Bilibili Video Transport System

## What This Project Does

Automatically discovers YouTube videos, generates Chinese titles/descriptions with an AI persona ("傲娇AI"), and uploads them to Bilibili. The AI persona evolves its own prompts through feedback loops.

## Current Development Status

**Phase: Fast Prototype → 准备 Formalize**

### 已完成（fast prototype，手动测试）
- Frontend dashboard: Pipeline 控制 / 字幕管理 / AI进化 三个 tab
- AI 进化系统: Skill 版本化、反思（yield/outcome/annotation）、审批流程、版本回滚
- 策略通过率统计: review 数据聚合，注入 yield 反思
- 弹幕生成: Go 调 Python subprocess (annotate_cli)，AnnotationSkill 自进化
- Loop 2 数据采集: B站播放量回流（已修复 412 header 问题）
- 反思审批: 反思不再自动生效，需人工批准/编辑/丢弃

### 架构迁移进度
> 详见 `docs/python-orchestrator-refactor.md`

- [x] **Stage 1: 前端单入口** — Python 代理 Go 端点，前端统一 `/api` 入口，Vite 单代理
- [x] **Stage 2: 多 Session + SSE + asyncio** — PipelineSession + asyncio.Event + SSE 推送 + LLM 信号量 + Go watchdog
- [x] **Stage 3: Review 快照 + Go 元数据扩展** — review_snapshots 崩溃恢复、upload_jobs 加 persona_id/strategy_name
- [x] **Stage 4: 清理** — 文档更新、废弃代码移除

### 未实现
- Historian 跨人设学习（等数据）
- 打分系统修复（opportunity_score 常量、tsundere 权重）
- Skill 命名空间（`sarcastic_ai::strategy_generation`）
- 弹幕编辑闭环（前端编辑弹幕 → feedback → 反思）
- 自动化测试（前端零测试，新 API 端点零测试）

## Architecture (Current — 架构迁移全部完成)

```
Frontend (React/Vite :5173)
    └── /api/* ──→ Python (:8000)  唯一入口
                     ├── Pipeline (asyncio + SSE 推送, 多 Session)
                     ├── LLM 信号量并发控制
                     ├── Skill CRUD, Review stats (data.db)
                     ├── Go 代理层 (/api/uploads/* → Go)
                     └── Go (:8081)  上传工具服务（Python 自动启停 + watchdog）
                           ├── Job queue (download → upload → subtitle)
                           └── metadata.db
```

## Development Workflow

This project follows a **fast prototype + formalize** workflow:
1. New features are built as fast prototypes with manual testing
2. After validation, they are formalized with automated tests and documentation
3. Progress is tracked in `docs/fast-prototype-report.md`

### Running Locally (Current)

```bash
# Go service
go run ./cmd/yttransfer --db-path metadata.db --ml-service-dir ml-service --llm-backend ollama

# Python service (will be removed after migration)
cd ml-service && .venv/Scripts/python -m uvicorn app.server:app --port 8000

# Frontend
cd frontend && npm run dev
```

### Key Commands

```bash
# Run Python tests (from ml-service/)
.venv/Scripts/python -m pytest tests/ -x

# Build Go
go build ./cmd/yttransfer

# Type-check frontend
cd frontend && npx tsc --noEmit
```

## Design Documents

| Doc | Location | Status |
|-----|----------|--------|
| Python orchestrator refactor | `docs/python-orchestrator-refactor.md` | **Next** — 架构迁移方案 |
| Fast prototype report | `docs/fast-prototype-report.md` | **Active** — 当前状态 + formalize 计划 |
| Subtitle pipeline | `docs/subtitle-pipeline.md` | **已更新** — whisper_autosrt + annotate_cli + 人工审批 |
| Persona-centric refactor | `ml-service/docs/persona-centric-refactor.md` | 部分实现，有偏差（见 prototype report） |
| Sarcastic AI persona | `ml-service/docs/sarcastic-ai-persona-design.md` | 已实现 |
| Historian (M4) | `ml-service/docs/m4-historian-design.md` | 未实现，等数据 |
| Multi-user chat | `docs/architecture.md` | 未来规划，不急 |

## Session Reports

每次 Claude Code session 结束前（用户说"就这样"、工作告一段落、或明确要求总结时），**必须**在 `docs/session-reports/` 下写一份报告。文件命名：`YYYY-MM-DD-简短描述.md`。

报告模板：

```markdown
# Session Report: <简短标题>
日期: YYYY-MM-DD

## 本次实现了什么
- （逐条列出完成的功能/修复/重构）

## 技术栈 & Workaround
- （用了什么库/工具、遇到的坑、绕过方案）

## 架构变更
- （是否偏离了原设计文档？改了哪些接口/数据流/模块边界？为什么？）
- 如果没有架构变更，写"无"

## 遗留问题 / TODO
- （本次没做完的、发现的新问题）
```

规则：
- 报告要**具体**，不要泛泛而谈。列出改了哪些文件、加了哪些端点、改了哪些表。
- 架构变更部分要对照 `docs/python-orchestrator-refactor.md` 和 `docs/fast-prototype-report.md` 说明偏差。
- 如果 session 只是小修小补（< 3 个文件改动、无架构影响），可以跳过不写。

## Important Conventions

- **Skill evolution**: Reflections are proposed, not auto-applied. Human reviews and approves/edits/discards via the AI进化 tab. Pipeline end-of-run does NOT auto-reflect (disabled until system stabilizes).
- **Encoding**: Windows environment — use `encoding="utf-8"` for file handlers, `PYTHONUTF8=1` for subprocesses.
- **SQLite ordering**: Use `ORDER BY id DESC` not `ORDER BY created_at DESC` (timestamp resolution issue).
- **Annotation**: Go calls Python as subprocess (`python -m app.annotate_cli`), not HTTP.
- **Data ownership**: Go owns `metadata.db`（upload_jobs, uploads），Python owns `data.db`（strategies, skills, reviews）。保持分离。

## Database Schema Summary

**Go — `metadata.db`**:
- `upload_jobs` (status, bvid, subtitle_status, deleted, persona_id, strategy_name)
- `uploads` (video_id, bvid)

**Python — `data.db`**:
- `strategies`, `strategy_runs` — 策略定义 + 执行记录
- `skills`, `skill_versions` — 自进化 prompt + 版本历史
- `review_decisions` — 人工审核记录（按策略可聚合通过率）
- `annotation_feedback` — 弹幕编辑反馈
- `scoring_params` — 评分参数
