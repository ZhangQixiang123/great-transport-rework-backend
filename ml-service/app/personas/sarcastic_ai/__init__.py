"""SarcasticAI (傲娇AI) persona — first persona implementation.

7-phase pipeline:
  1. Strategy generation (LLM generates YouTube search queries)
  2. Market analysis (check Bilibili saturation)
  3. Agentic YouTube search (search + score + dedup)
  4. Transportability check (safety filter + LLM judgment + persona fit)
  5. Copy generation (persona-flavored title + description)
  6. Human review (dry-run: print; live: interactive)
  7. Upload (submit to Go service)
"""
import asyncio
import json
import logging
from typing import Optional

from app.db import Database
from app.llm import create_backend
from app.personas.protocol import Persona, RunContext, RunResult, PerformanceSummary
from app.personas._shared.transportability import check_transportability
from app.personas._shared.upload import submit_upload, get_uploaded_ids
from app.personas._shared.tags import generate_tags
from app.personas._shared.aggregator import SearchAggregator
from app.personas._shared.scoring import ScoringParams, heuristic_score
from app.personas._shared.outcomes import OutcomeTracker
from app.skills import StrategyGenerationSkill, MarketAnalysisSkill

from .config import (
    PERSONA_ID, SEARCH_IDENTITY, CONTENT_AFFINITY,
    PERSONA_FIT_PROMPT, PERSONA_FIT_THRESHOLD,
)
from .prompts import (
    SYSTEM_PROMPT, STRATEGY_HINTS,
    sample_few_shot, get_intensity, get_temperature,
)
from .strategies import bootstrap_strategies, bootstrap_scoring, validate_query, validate_result

logger = logging.getLogger(__name__)


class SarcasticAI:
    """傲娇AI persona implementation."""

    @property
    def persona_id(self) -> str:
        return PERSONA_ID

    async def run(self, db: Database, context: RunContext) -> RunResult:
        """Execute the full 7-phase discovery+upload pipeline."""
        import os
        result = RunResult(persona_id=self.persona_id)
        backend_type = os.environ.get("LLM_BACKEND", "ollama")
        backend = create_backend(backend_type=backend_type)
        tracker = OutcomeTracker(db)

        # Self-bootstrap (idempotent)
        bootstrap_strategies(db, persona_id=self.persona_id)
        bootstrap_scoring(db, persona_id=self.persona_id)

        try:
            # ── Phase 1: Strategy Generation ─────────────────────────
            logger.info("[%s] Phase 1: Strategy generation", self.persona_id)
            strategies = db.list_strategies(active_only=True, persona_id=self.persona_id)

            strategy_skill = StrategyGenerationSkill(db=db, backend=backend)
            strategies_context = strategy_skill.format_strategies_context(strategies)
            recent_outcomes = strategy_skill.format_recent_outcomes(
                db.get_latest_run_yields(limit=30, persona_id=self.persona_id)
            )
            gen_result = strategy_skill.execute({
                "strategies_with_full_context": strategies_context,
                "recent_outcomes_with_youtube_context": recent_outcomes,
                "hot_words": "(not available)",
            })
            queries = gen_result.get("queries", [])

            # Validate queries against strategy constraints
            valid_queries = []
            for q in queries:
                query_text = q.get("query", "") if isinstance(q, dict) else q
                strategy_name = q.get("strategy_name", "unknown") if isinstance(q, dict) else "unknown"
                if validate_query(strategy_name, query_text):
                    valid_queries.append(q)
                else:
                    logger.warning("[%s] DROPPED query (failed constraint): [%s] %s",
                                   self.persona_id, strategy_name, query_text)
            queries = valid_queries

            logger.info("[%s] Generated %d queries (%d dropped by constraints)",
                        self.persona_id, len(queries), len(gen_result.get("queries", [])) - len(queries))

            if not queries:
                logger.warning("[%s] No queries generated, aborting", self.persona_id)
                return result

            # Filter by selected strategies (if specified)
            if context.selected_strategies is not None:
                before = len(queries)
                queries = [q for q in queries
                           if (q.get("strategy_name", "unknown") if isinstance(q, dict) else "unknown")
                           in context.selected_strategies]
                logger.info("[%s] Strategy filter: %d → %d queries (selected: %s)",
                            self.persona_id, before, len(queries),
                            ", ".join(sorted(context.selected_strategies)))
                if not queries:
                    logger.warning("[%s] No queries after strategy filter, aborting", self.persona_id)
                    return result

            # ── Phase 2: Market Analysis ─────────────────────────────
            logger.info("[%s] Phase 2: Market analysis (skipped in dry-run for speed)", self.persona_id)
            # In dry-run mode, skip Bilibili API calls
            # In live mode, this would check saturation per query

            # ── Phase 3: Agentic YouTube Search ──────────────────────
            logger.info("[%s] Phase 3: YouTube search", self.persona_id)
            from app.personas._shared.youtube import search_youtube_videos

            aggregator = SearchAggregator()
            already_seen = context.global_seen_ids | db.get_already_transported_yt_ids()

            try:
                already_seen |= get_uploaded_ids(context.go_url)
            except Exception:
                pass

            # Load scoring params
            params_row = db.get_scoring_params(persona_id=self.persona_id)
            if params_row:
                scoring_params = ScoringParams.from_json(params_row["params_json"])
            else:
                scoring_params = ScoringParams()

            quota_used = 0
            for q in queries:
                if quota_used >= context.quota_budget:
                    logger.info("[%s] Quota budget exhausted", self.persona_id)
                    break

                query_text = q.get("query", "") if isinstance(q, dict) else q
                strategy_name = q.get("strategy_name", "unknown") if isinstance(q, dict) else "unknown"

                if not query_text:
                    continue

                # Record strategy run
                strategy_row = db.get_strategy(strategy_name, persona_id=self.persona_id)
                strategy_id = strategy_row["id"] if strategy_row else None
                if strategy_id:
                    run_id = db.save_strategy_run(
                        strategy_id, query_text,
                        persona_id=self.persona_id,
                        bilibili_check=q.get("bilibili_check") if isinstance(q, dict) else None,
                    )
                else:
                    run_id = None

                candidates = search_youtube_videos(query_text, max_results=10, max_age_days=90)
                quota_used += 100

                avg_views = 0
                if candidates:
                    avg_views = sum(c.views for c in candidates) // len(candidates)

                best = None
                for c in candidates:
                    if c.video_id in already_seen:
                        continue
                    if c.views < scoring_params.youtube_min_views:
                        continue
                    if not validate_result(strategy_name, c.title):
                        logger.debug("[%s] SKIP result (off-strategy): [%s] %s",
                                     self.persona_id, strategy_name, c.title[:60])
                        continue

                    score = heuristic_score(
                        c.views, c.likes, c.duration_seconds,
                        c.category_id, 0.5, scoring_params,
                    )
                    # Apply content affinity bonus
                    affinity = CONTENT_AFFINITY.get(c.category_id, 0.5)
                    score *= affinity

                    aggregator.add(
                        video_id=c.video_id,
                        title=c.title,
                        channel=c.channel_title,
                        views=c.views,
                        likes=c.likes,
                        duration_seconds=c.duration_seconds,
                        category_id=c.category_id,
                        opportunity_score=score,
                        strategy=strategy_name,
                        query=query_text,
                    )
                    already_seen.add(c.video_id)

                    if best is None or c.views > best.get("views", 0):
                        best = {
                            "id": c.video_id, "title": c.title,
                            "channel": c.channel_title, "views": c.views,
                            "likes": c.likes, "category_id": c.category_id,
                            "duration_seconds": c.duration_seconds,
                        }

                # Record yield
                if run_id:
                    tracker.record_query_yield(
                        run_id, len(candidates), avg_views, best,
                    )

            result.videos_discovered = aggregator.count()
            logger.info("[%s] Discovered %d unique candidates", self.persona_id, result.videos_discovered)

            # ── Phase 4: Transportability Check ──────────────────────
            logger.info("[%s] Phase 4: Transportability check", self.persona_id)
            top_candidates = aggregator.get_candidates(min_views=scoring_params.youtube_min_views)[:20]
            approved = []

            for c in top_candidates:
                check = check_transportability(
                    backend=backend,
                    title=c.title,
                    channel=c.channel,
                    duration_seconds=c.duration_seconds,
                    category_id=c.category_id,
                    persona_fit_prompt=PERSONA_FIT_PROMPT,
                    persona_fit_threshold=PERSONA_FIT_THRESHOLD,
                )
                if check["transportable"]:
                    approved.append((c, check))
                    logger.info("[%s] APPROVED: %s (fit=%.2f)", self.persona_id, c.title[:50], check["persona_fit"])
                else:
                    result.videos_rejected += 1
                    logger.info("[%s] REJECTED: %s — %s", self.persona_id, c.title[:50], check["reasoning"][:80])

            logger.info("[%s] %d approved, %d rejected", self.persona_id, len(approved), result.videos_rejected)

            # ── Phase 5: Copy Generation ─────────────────────────────
            logger.info("[%s] Phase 5: Copy generation", self.persona_id)
            upload_jobs = []

            # Load past approved examples as dynamic few-shot
            dynamic_examples = _build_dynamic_examples(db, self.persona_id)
            if dynamic_examples:
                logger.info("[%s] Loaded %d dynamic few-shot from past reviews",
                            self.persona_id, len(dynamic_examples))

            for candidate, check in approved:
                strategy_name = candidate.source_strategies[0] if candidate.source_strategies else "unknown"
                hint = STRATEGY_HINTS.get(strategy_name, "")
                # 2 hardcoded + up to 1 dynamic = 3 few-shot
                static_examples = sample_few_shot(strategy_name, count=2)
                temperature = get_temperature(strategy_name)

                copy_prompt = (
                    f"\u539f\u6807\u9898\uff1a{candidate.title}\n"
                    f"\u9891\u9053\uff1a{candidate.channel}\n"
                    f"YouTube\u64ad\u653e\u91cf\uff1a{candidate.views:,}\u6b21\u89c2\u770b\n"
                    f"\u65f6\u957f\uff1a{candidate.duration_seconds // 60}\u5206{candidate.duration_seconds % 60}\u79d2\n"
                    f"\u641c\u7d22\u7b56\u7565\uff1a{strategy_name}\n"
                    f"\u7b56\u7565\u63d0\u793a\uff1a{hint}"
                )

                messages = [
                    {"role": "system", "content": SYSTEM_PROMPT},
                ]
                # Hardcoded few-shot
                for ex in static_examples:
                    messages.append({"role": "user", "content": ex["input"]})
                    messages.append({"role": "assistant", "content": ex["output"]})
                # Dynamic few-shot from past approved reviews (prefer same strategy)
                dyn = _pick_dynamic_example(dynamic_examples, strategy_name)
                if dyn:
                    messages.append({"role": "user", "content": dyn["input"]})
                    messages.append({"role": "assistant", "content": dyn["output"]})
                # Add the actual request
                messages.append({"role": "user", "content": copy_prompt})

                response = backend.chat(messages=messages, temperature=temperature)
                title, desc, tsundere = _parse_copy_response(response)

                if not title:
                    logger.warning("[%s] Failed to parse copy for %s", self.persona_id, candidate.video_id)
                    continue

                upload_jobs.append({
                    "video_id": candidate.video_id,
                    "title": title,
                    "description": desc,
                    "strategy": strategy_name,
                    "candidate": candidate,
                    "tsundere_score": tsundere,
                })

            # ── Phase 5b: Rank and select top 3 ───────────────────
            if upload_jobs:
                upload_jobs = _rank_and_select(upload_jobs, top_n=3)
                logger.info("[%s] Selected top %d from %d candidates",
                            self.persona_id, len(upload_jobs),
                            len(approved))

            # ── Phase 6: Human Review ──────────────────────────────
            logger.info("[%s] Phase 6: Review — %d jobs ready", self.persona_id, len(upload_jobs))

            if context.no_review:
                logger.info("[%s] Skipping review (--no-review), auto-approving all", self.persona_id)
                for job in upload_jobs:
                    db.save_review_decision(
                        persona_id=self.persona_id,
                        strategy_run_id=None,
                        youtube_video_id=job["video_id"],
                        strategy_name=job.get("strategy", "unknown"),
                        decision="approved",
                        original_title=job["candidate"].title,
                        original_desc="",
                        final_title=job["title"],
                        final_desc=job["description"],
                    )
            else:
                from app.personas._shared.review import interactive_review
                regenerate = _make_regenerate_fn(backend)
                upload_jobs = interactive_review(
                    jobs=upload_jobs,
                    regenerate_fn=regenerate,
                    persona_id=self.persona_id,
                    db=db,
                )

            # ── Phase 7: Upload ──────────────────────────────────────
            if context.dry_run or context.no_upload:
                logger.info("[%s] Phase 7: Upload SKIPPED (--dry-run/--no-upload)", self.persona_id)
            else:
                logger.info("[%s] Phase 7: Upload", self.persona_id)
                for job in upload_jobs:
                    # Generate tags
                    tags = []
                    try:
                        tags = await generate_tags(job["title"], max_tags=10)
                    except Exception as e:
                        logger.warning("[%s] Tag generation failed: %s", self.persona_id, e)

                    resp = submit_upload(
                        go_url=context.go_url,
                        video_id=job["video_id"],
                        title=job["title"],
                        description=job["description"],
                        tags=",".join(tags),
                    )

                    if resp.get("status") not in ("failed",):
                        result.videos_uploaded += 1
                        logger.info("[%s] Uploaded: %s", self.persona_id, job["title"][:50])
                    else:
                        result.errors.append(f"Upload failed for {job['video_id']}: {resp.get('error')}")

            # ── Yield reflection (Loop 1) ────────────────────────────
            logger.info("[%s] Running yield reflection", self.persona_id)
            yield_data = db.get_latest_run_yields(limit=50, persona_id=self.persona_id)
            strategy_stats = db.get_strategy_yield_stats(persona_id=self.persona_id)
            strategy_skill.reflect_on_yield(yield_data, strategy_stats)

        except Exception as e:
            logger.error("[%s] Pipeline error: %s", self.persona_id, e, exc_info=True)
            result.errors.append(str(e))

        return result

    def apply_historian_update(self, db: Database, summary: PerformanceSummary) -> list[str]:
        """Apply historian-generated updates to persona config."""
        updates = []
        # Future: update few-shot examples, adjust thresholds, etc.
        return updates

    def _dry_run_summary(self, queries: list, gen_result: dict) -> None:
        """Print a summary for dry-run mode."""
        print(f"\n{'='*60}")
        print(f"SarcasticAI DRY-RUN Summary")
        print(f"{'='*60}")
        print(f"Queries generated: {len(queries)}")

        by_strategy = {}
        for q in queries:
            name = q.get("strategy_name", "unknown") if isinstance(q, dict) else "unknown"
            by_strategy.setdefault(name, []).append(q)

        for strategy, qs in sorted(by_strategy.items()):
            print(f"\n  [{strategy}] ({len(qs)} queries)")
            for q in qs[:3]:
                query_text = q.get("query", "") if isinstance(q, dict) else q
                print(f"    - {query_text}")
            if len(qs) > 3:
                print(f"    ... and {len(qs) - 3} more")

        proposals = gen_result.get("new_strategy_proposals", [])
        if proposals:
            print(f"\n  New strategy proposals: {len(proposals)}")
            for p in proposals:
                print(f"    - {p.get('name', '?')}: {p.get('description', '')[:60]}")

        retires = gen_result.get("retire_suggestions", [])
        if retires:
            print(f"\n  Retire suggestions: {', '.join(retires)}")

        print(f"{'='*60}\n")


DEEP_CONTENT_CATEGORIES = {27, 28}  # Education, Science & Tech


def _rank_and_select(jobs: list[dict], top_n: int = 3) -> list[dict]:
    """Rank upload jobs by composite score and return top N.

    Scoring:
    - Views (log-scaled, 0-1): higher is better
    - Duration bonus (0 or 1): < 180s unless deep tech/education
    - Tsundere score (0-1): from LLM self-rating
    """
    import math

    for job in jobs:
        c = job["candidate"]
        # View score: log-scaled, capped at 1M
        view_score = min(1.0, math.log1p(c.views) / math.log1p(1_000_000))

        # Duration: prefer short unless deep content
        if c.category_id in DEEP_CONTENT_CATEGORIES:
            dur_score = 1.0  # no penalty for deep content
        elif c.duration_seconds <= 180:
            dur_score = 1.0
        elif c.duration_seconds <= 600:
            dur_score = 0.5
        else:
            dur_score = 0.2

        # Tsundere score normalized to 0-1
        tsundere_norm = job.get("tsundere_score", 5) / 10.0

        # Composite: tsundere most important, then views, then duration
        job["_rank_score"] = (
            tsundere_norm * 0.5
            + view_score * 0.3
            + dur_score * 0.2
        )

    jobs.sort(key=lambda j: j["_rank_score"], reverse=True)
    return jobs[:top_n]


def _queries_from_strategies(strategies: list[dict]) -> list[dict]:
    """Extract example queries from DB strategy rows for dry-run mode."""
    import json as _json
    queries = []
    for s in strategies:
        raw = s.get("example_queries", "[]")
        try:
            example_list = _json.loads(raw) if raw else []
        except (ValueError, TypeError):
            example_list = []
        for eq in example_list:
            queries.append({
                "query": eq,
                "strategy_name": s["name"],
                "bilibili_check": s.get("bilibili_check", ""),
            })
    return queries


def _build_dynamic_examples(db, persona_id: str) -> list[dict]:
    """Load past approved/revised reviews as dynamic few-shot examples.

    Revised examples (user gave feedback) are prioritized — they represent
    human-corrected persona voice, the best training signal we have.
    """
    rows = db.get_approved_examples(persona_id, limit=30)
    examples = []
    for r in rows:
        if not r["final_title"] or not r["final_desc"]:
            continue
        examples.append({
            "input": f"\u539f\u6807\u9898\uff1a{r['original_title']}\n\u641c\u7d22\u7b56\u7565\uff1a{r['strategy_name']}",
            "output": f"\u6807\u9898\uff1a{r['final_title']}\n\u7b80\u4ecb\uff1a{r['final_desc']}",
            "strategy": r["strategy_name"],
            "is_revised": r["decision"] == "revised",
        })
    return examples


def _pick_dynamic_example(examples: list[dict], strategy_name: str):
    """Pick one dynamic few-shot example, preferring same strategy + revised."""
    if not examples:
        return None
    import random
    # Prefer: same strategy & revised > same strategy > revised > any
    same_revised = [e for e in examples if e["strategy"] == strategy_name and e["is_revised"]]
    if same_revised:
        return random.choice(same_revised)
    same_strat = [e for e in examples if e["strategy"] == strategy_name]
    if same_strat:
        return random.choice(same_strat)
    revised = [e for e in examples if e["is_revised"]]
    if revised:
        return random.choice(revised)
    return random.choice(examples)


def _parse_copy_response(response: str) -> tuple[str, str, int]:
    """Parse the LLM copy response into (title, description, tsundere_score)."""
    title = ""
    desc = ""
    tsundere = 5  # default if not parsed

    for line in response.strip().split("\n"):
        line = line.strip()
        if line.startswith("标题：") or line.startswith("标题:"):
            title = line.split("：", 1)[-1].split(":", 1)[-1].strip()
        elif line.startswith("简介：") or line.startswith("简介:"):
            desc = line.split("：", 1)[-1].split(":", 1)[-1].strip()
        elif line.startswith("傲娇指数：") or line.startswith("傲娇指数:"):
            raw = line.split("：", 1)[-1].split(":", 1)[-1].strip()
            # Extract number from "8" or "8/10" etc.
            import re
            m = re.search(r"(\d+)", raw)
            if m:
                tsundere = min(10, max(1, int(m.group(1))))
        elif title and not desc:
            pass
        elif desc and not line.startswith("傲娇"):
            desc += line

    return title, desc, tsundere


REVISION_INSTRUCTION = (
    "\u4e3b\u4eba\u5bf9\u4f60\u7684\u6587\u6848\u6709\u4fee\u6539\u610f\u89c1\u3002"
    "\u89c4\u5219\uff1a\n"
    "1. \u53ea\u6539\u4e3b\u4eba\u6307\u51fa\u7684\u95ee\u9898\uff0c\u5176\u4ed6\u90e8\u5206\u4fdd\u6301\u4e0d\u53d8\u3002\n"
    "2. \u4e0d\u8981\u4e22\u5931\u4eba\u8bbe\u8154\u2014\u2014\u4f60\u4f9d\u7136\u662f\u90a3\u4e2a\u50b2\u5a07\u7684AI\uff0c\u4e0d\u662f\u666e\u901a\u642c\u8fd0\u53f7\u3002\n"
    "3. \u4fee\u6539\u540e\u7684\u6587\u6848\u5fc5\u987b\u548c\u4fee\u6539\u524d\u7684\u903b\u8f91\u4e00\u81f4\uff0c\u4e0d\u80fd\u51fa\u73b0\u77db\u76fe\u3002\n"
    "4. \u4fdd\u6301\u76f8\u540c\u7684\u683c\u5f0f\u8f93\u51fa\uff08\u6807\u9898\u3001\u7b80\u4ecb\u3001\u50b2\u5a07\u6307\u6570\uff09\u3002\n\n"
)


def _make_regenerate_fn(backend):
    """Create a regenerate callback for interactive review.

    Uses the persona's SYSTEM_PROMPT + few-shot + constrained revision prompt
    to ask the LLM to fix only what was requested while staying in character.
    """
    def regenerate(job, feedback, prev_title, prev_desc):
        strategy_name = job.get("strategy", "unknown")
        hint = STRATEGY_HINTS.get(strategy_name, "")
        examples = sample_few_shot(strategy_name, count=2)
        temperature = get_temperature(strategy_name)
        c = job["candidate"]

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
        ]
        for ex in examples:
            messages.append({"role": "user", "content": ex["input"]})
            messages.append({"role": "assistant", "content": ex["output"]})

        # Original request
        copy_prompt = (
            f"\u539f\u6807\u9898\uff1a{c.title}\n"
            f"\u9891\u9053\uff1a{c.channel}\n"
            f"YouTube\u64ad\u653e\u91cf\uff1a{c.views:,}\u6b21\u89c2\u770b\n"
            f"\u65f6\u957f\uff1a{c.duration_seconds // 60}\u5206{c.duration_seconds % 60}\u79d2\n"
            f"\u641c\u7d22\u7b56\u7565\uff1a{strategy_name}\n"
            f"\u7b56\u7565\u63d0\u793a\uff1a{hint}"
        )
        messages.append({"role": "user", "content": copy_prompt})

        # Previous attempt
        prev_output = (
            f"\u6807\u9898\uff1a{prev_title}\n"
            f"\u7b80\u4ecb\uff1a{prev_desc}"
        )
        messages.append({"role": "assistant", "content": prev_output})

        # Constrained revision request
        messages.append({
            "role": "user",
            "content": (
                f"{REVISION_INSTRUCTION}"
                f"\u4e3b\u4eba\u7684\u4fee\u6539\u610f\u89c1\uff1a{feedback}\n\n"
                f"\u8bf7\u53ea\u6839\u636e\u4ee5\u4e0a\u610f\u89c1\u4fee\u6539\uff0c\u4e0d\u8981\u6539\u52a8\u6ca1\u6709\u88ab\u6307\u51fa\u7684\u90e8\u5206\u3002"
            ),
        })

        response = backend.chat(messages=messages, temperature=temperature)
        return _parse_copy_response(response)

    return regenerate
