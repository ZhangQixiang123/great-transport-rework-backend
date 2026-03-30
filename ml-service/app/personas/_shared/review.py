"""Interactive human review — stateless tool for Phase 6.

The persona provides a regenerate_fn callback to control LLM/prompt/temperature.
This module handles terminal I/O, timing, and DB recording.
"""
import io
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from app.db import Database


@dataclass
class ReviewDecision:
    video_id: str
    decision: str  # "approved" | "rejected" | "revised"
    final_title: str
    final_desc: str
    final_tsundere: int
    reject_reason: str = ""
    feedback_rounds: list = field(default_factory=list)
    review_time_seconds: float = 0.0


def _make_utf8_out():
    """Create a UTF-8 text wrapper around stdout for Chinese output on Windows."""
    return io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def _display_job(out, job: dict, index: int, total: int) -> None:
    """Display a single candidate for review."""
    c = job["candidate"]
    out.write(f"\n{'='*60}\n")
    out.write(f"  #{index}/{total}  [{job['strategy']}]\n")
    out.write(f"  \u539f\u6807\u9898: {c.title}\n")
    out.write(f"  \u9891\u9053: {c.channel}  |  \u64ad\u653e: {c.views:,}  |  \u65f6\u957f: {c.duration_seconds//60}m{c.duration_seconds%60}s\n")
    out.write(f"  {'─'*40}\n")
    out.write(f"  B\u7ad9\u6807\u9898: {job['title']}\n")
    out.write(f"  B\u7ad9\u7b80\u4ecb: {job['description']}\n")
    out.write(f"  \u50b2\u5a07\u6307\u6570: {job.get('tsundere_score', '?')}/10\n")
    out.write(f"{'='*60}\n")
    out.write(f"  [y] \u901a\u8fc7  [e] \u8981\u6c42\u4fee\u6539  [n] \u62d2\u7edd  [q] \u8df3\u8fc7\u5269\u4f59\u5168\u90e8\u62d2\u7edd\n")
    out.flush()


def _display_revised(out, title: str, desc: str, tsundere: int) -> None:
    """Display revised copy after regeneration."""
    out.write(f"\n  {'─'*40}\n")
    out.write(f"  \u65b0\u6807\u9898: {title}\n")
    out.write(f"  \u65b0\u7b80\u4ecb: {desc}\n")
    out.write(f"  \u50b2\u5a07\u6307\u6570: {tsundere}/10\n")
    out.write(f"  {'─'*40}\n")
    out.write(f"  [y] \u901a\u8fc7  [e] \u7ee7\u7eed\u4fee\u6539  [n] \u62d2\u7edd\n")
    out.flush()


def interactive_review(
    jobs: list[dict],
    regenerate_fn: Callable,
    persona_id: str,
    db: Database,
    max_revisions: int = 3,
) -> list[dict]:
    """Interactively review each candidate video.

    Args:
        jobs: upload_jobs from Phase 5b (ranked, top N).
        regenerate_fn: Persona callback (job, feedback, prev_title, prev_desc) -> (title, desc, tsundere).
        persona_id: For DB recording.
        db: Database instance.
        max_revisions: Max revision rounds per video.

    Returns:
        List of approved jobs (title/desc may have been revised).
    """
    if not jobs:
        return []

    approved_jobs = []
    out = _make_utf8_out()
    total = len(jobs)
    quit_all = False

    for idx, job in enumerate(jobs, 1):
        if quit_all:
            _record_decision(db, persona_id, job, "rejected",
                             final_title=job["title"],
                             final_desc=job["description"],
                             reject_reason="skipped (quit all)")
            continue

        start_time = time.monotonic()
        rounds = []
        current_title = job["title"]
        current_desc = job["description"]
        current_tsundere = job.get("tsundere_score", 5)
        revision_count = 0

        _display_job(out, job, idx, total)

        while True:
            choice = input("  > ").strip().lower()

            if choice == "y":
                elapsed = time.monotonic() - start_time
                job["title"] = current_title
                job["description"] = current_desc
                job["tsundere_score"] = current_tsundere
                decision = "revised" if rounds else "approved"
                _record_decision(
                    db, persona_id, job, decision,
                    final_title=current_title,
                    final_desc=current_desc,
                    feedback_rounds=rounds,
                    review_time=elapsed,
                )
                approved_jobs.append(job)
                out.write(f"  \u2713 \u901a\u8fc7\n")
                out.flush()
                break

            elif choice == "n":
                elapsed = time.monotonic() - start_time
                reason = input("  \u62d2\u7edd\u539f\u56e0 (\u53ef\u7559\u7a7a): ").strip()
                _record_decision(
                    db, persona_id, job, "rejected",
                    final_title=current_title,
                    final_desc=current_desc,
                    reject_reason=reason,
                    feedback_rounds=rounds,
                    review_time=elapsed,
                )
                out.write(f"  \u2717 \u5df2\u62d2\u7edd\n")
                out.flush()
                break

            elif choice == "e":
                if revision_count >= max_revisions:
                    out.write(f"  \u5df2\u8fbe\u4fee\u6539\u4e0a\u9650 ({max_revisions}\u6b21)\uff0c\u8bf7\u9009\u62e9 [y] \u6216 [n]\n")
                    out.flush()
                    continue

                feedback = input("  \u4fee\u6539\u610f\u89c1: ").strip()
                if not feedback:
                    out.write("  \u8bf7\u8f93\u5165\u4fee\u6539\u610f\u89c1\n")
                    out.flush()
                    continue

                out.write("  \u91cd\u65b0\u751f\u6210\u4e2d...\n")
                out.flush()

                new_title, new_desc, new_tsundere = regenerate_fn(
                    job, feedback, current_title, current_desc,
                )

                rounds.append({
                    "feedback": feedback,
                    "prev_title": current_title,
                    "prev_desc": current_desc,
                    "new_title": new_title,
                    "new_desc": new_desc,
                    "tsundere": new_tsundere,
                })

                current_title = new_title
                current_desc = new_desc
                current_tsundere = new_tsundere
                revision_count += 1

                _display_revised(out, new_title, new_desc, new_tsundere)

            elif choice == "q":
                elapsed = time.monotonic() - start_time
                _record_decision(
                    db, persona_id, job, "rejected",
                    final_title=current_title,
                    final_desc=current_desc,
                    reject_reason="skipped (quit all)",
                    feedback_rounds=rounds,
                    review_time=elapsed,
                )
                quit_all = True
                out.write(f"  \u8df3\u8fc7\u5269\u4f59\u5168\u90e8\n")
                out.flush()
                break

            else:
                out.write("  \u8bf7\u8f93\u5165 y/e/n/q\n")
                out.flush()

    out.write(f"\n\u5ba1\u6838\u5b8c\u6210: {len(approved_jobs)}/{total} \u901a\u8fc7\n")
    out.flush()
    out.detach()

    return approved_jobs


def _record_decision(
    db: Database,
    persona_id: str,
    job: dict,
    decision: str,
    final_title: str = "",
    final_desc: str = "",
    reject_reason: str = "",
    feedback_rounds: list = None,
    review_time: float = 0.0,
) -> None:
    """Write review decision to DB."""
    db.save_review_decision(
        persona_id=persona_id,
        strategy_run_id=None,
        youtube_video_id=job["video_id"],
        strategy_name=job.get("strategy", "unknown"),
        decision=decision,
        original_title=job["candidate"].title,
        original_desc="",
        final_title=final_title or job.get("title", ""),
        final_desc=final_desc or job.get("description", ""),
        feedback_rounds_json=json.dumps(feedback_rounds or [], ensure_ascii=False),
        reject_reason=reject_reason,
        review_time_seconds=review_time,
    )
