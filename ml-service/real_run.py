"""Entry point for persona-centric pipeline.

Usage:
    python real_run.py --dry-run                  # all personas, dry run
    python real_run.py --persona sarcastic_ai     # single persona, live
    python real_run.py --dry-run --verbose         # debug output
"""
import os
os.environ.setdefault("PYTHONUTF8", "1")

import argparse
import asyncio
import logging
import sys

from app.db import Database
from app.personas import PersonaOrchestrator, ALL_PERSONAS
from app.personas.protocol import RunContext, RunResult
from app.personas.sarcastic_ai import SarcasticAI


def main():
    parser = argparse.ArgumentParser(description="Persona-centric discovery pipeline")
    parser.add_argument("--db-path", default="data.db", help="SQLite database path")
    parser.add_argument("--persona", type=str, default=None,
                        help="Run only this persona (e.g. sarcastic_ai)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip YouTube API calls and uploads")
    parser.add_argument("--no-upload", action="store_true",
                        help="Run full pipeline but skip upload to Go service")
    parser.add_argument("--no-review", action="store_true",
                        help="Skip human review step")
    parser.add_argument("--go-url", default="http://localhost:8081",
                        help="Go upload service URL")
    parser.add_argument("--quota-budget", type=int, default=2000,
                        help="YouTube API quota budget")
    parser.add_argument("--backend", default="ollama",
                        help="LLM backend (ollama, openai, anthropic)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Set backend env for create_backend()
    import os
    os.environ.setdefault("LLM_BACKEND", args.backend)

    db = Database(args.db_path)
    db.connect()
    db.ensure_all_tables()

    logger = logging.getLogger("real_run")

    # Build context
    context = RunContext(
        dry_run=args.dry_run,
        no_upload=args.no_upload,
        no_review=args.no_review,
        go_url=args.go_url,
        quota_budget=args.quota_budget,
    )

    # Select personas
    if args.persona:
        persona_map = {cls().persona_id: cls for cls in ALL_PERSONAS}
        if args.persona not in persona_map:
            print(f"Unknown persona: {args.persona}")
            print(f"Available: {', '.join(persona_map.keys())}")
            sys.exit(1)
        persona_classes = [persona_map[args.persona]]
    else:
        persona_classes = list(ALL_PERSONAS)

    orchestrator = PersonaOrchestrator(persona_classes=persona_classes)

    # Run
    results = asyncio.run(orchestrator.run_all(db, context))

    # Summary
    print(f"\n{'='*60}")
    print("Pipeline Complete")
    print(f"{'='*60}")
    for pid, r in results.items():
        print(f"\n  [{pid}]")
        print(f"    Discovered: {r.videos_discovered}")
        print(f"    Uploaded:   {r.videos_uploaded}")
        print(f"    Rejected:   {r.videos_rejected}")
        if r.errors:
            print(f"    Errors:     {len(r.errors)}")
            for e in r.errors[:3]:
                print(f"      - {e[:80]}")
    print(f"{'='*60}\n")

    db.close()


if __name__ == "__main__":
    main()
