# M4: Historian — Cross-Persona Learning

## Overview

The Historian is a system-level agent that observes outcomes across ALL personas
and generates actionable insights. It does NOT make changes directly — it
produces `PerformanceSummary` objects that each persona processes through
`apply_historian_update()`.

## Architecture

```
Orchestrator
  └── run_all()
        ├── persona_1.run() → RunResult
        ├── persona_2.run() → RunResult
        └── historian.analyze(all_results) → List[PerformanceSummary]
              ├── persona_1.apply_historian_update(summary)
              └── persona_2.apply_historian_update(summary)
```

## Historian Responsibilities

1. **Outcome aggregation** — Collect Bilibili view/engagement data for all
   transported videos (Loop 2 feedback). Query `review_decisions` +
   `competitor_videos` for post-transport metrics.

2. **Cross-persona dedup analysis** — Detect when multiple personas discover
   the same video. Track which persona's copy style performs better.

3. **Strategy effectiveness** — Compare yield rates and Bilibili outcomes
   across personas using the same strategy. Recommend strategy retirement
   or promotion.

4. **Scoring calibration** — Compare heuristic scores vs actual Bilibili
   performance. Suggest `ScoringParams` updates (category bonuses, view
   thresholds, duration sweet spots).

5. **Principle evolution** — Suggest updates to youtube_principles and
   bilibili_principles based on cross-persona outcome patterns.

## PerformanceSummary Schema

```python
class PerformanceSummary(BaseModel):
    persona_id: str
    period_start: datetime
    period_end: datetime

    # Outcomes
    total_transported: int = 0
    success_count: int = 0  # views > threshold
    failure_count: int = 0
    avg_bilibili_views: float = 0

    # Strategy insights
    best_strategies: list[str] = []
    worst_strategies: list[str] = []
    retire_suggestions: list[str] = []

    # Scoring feedback
    scoring_adjustments: dict = {}  # e.g. {"category_bonuses": {20: 1.8}}

    # Principle suggestions
    youtube_principle_updates: str = ""
    bilibili_principle_updates: str = ""

    # Cross-persona
    duplicate_video_ids: list[str] = []
    copy_style_comparison: str = ""
```

## Implementation Plan

### Files to create
- `app/personas/_shared/historian.py` — `Historian` class with `analyze()`
- Update `PerformanceSummary` in `protocol.py` with full schema

### Key methods
- `Historian.analyze(db, results: dict[str, RunResult]) -> dict[str, PerformanceSummary]`
- `Historian._collect_outcomes(db, persona_id, days=30) -> list[dict]`
- `Historian._detect_duplicates(db) -> list[str]`
- `Historian._generate_insights(backend, outcomes) -> dict` (LLM call)

### Integration
- Orchestrator calls `historian.analyze()` after all personas complete
- Each persona receives its summary via `apply_historian_update()`
- Persona decides what to actually change (Historian only suggests)

### Loop 2 timing
- Bilibili outcomes take 7-14 days to accumulate meaningful data
- Historian should only run when there are new outcomes since last analysis
- Store `last_historian_run` timestamp in DB

## Dependencies
- Requires M1 (Protocol + DB) ✅ done
- Requires M2 (Skills) ✅ done
- Requires M3 (at least one persona) ✅ done
- Requires real transport data (videos uploaded and metrics collected)

## Priority: Medium
Wait until we have real transport outcome data before implementing.
The reflection loops in StrategyGenerationSkill already handle per-persona
learning (Loop 1 yield, Loop 2 outcomes). The Historian adds cross-persona
learning on top.
