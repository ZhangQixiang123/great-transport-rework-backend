"""CLI entry point for the persona-centric ML service."""
import os
os.environ.setdefault("PYTHONUTF8", "1")

import argparse
import asyncio
import logging
import sys


def main():
    parser = argparse.ArgumentParser(description="Persona-centric ML service")
    parser.add_argument("--db-path", default="data.db", help="SQLite database path")
    parser.add_argument("--verbose", "-v", action="store_true")

    sub = parser.add_subparsers(dest="command")

    # Bootstrap
    boot = sub.add_parser("bootstrap", help="Initialize database tables and seed data")
    boot.add_argument("--backend", default=None, help="LLM backend for principle generation")

    # Run
    run = sub.add_parser("run", help="Run persona pipeline")
    run.add_argument("--persona", type=str, default=None, help="Run only this persona")
    run.add_argument("--dry-run", action="store_true", help="Skip API calls and uploads")
    run.add_argument("--no-review", action="store_true", help="Skip human review")
    run.add_argument("--go-url", default="http://localhost:8081", help="Go service URL")
    run.add_argument("--quota-budget", type=int, default=2000, help="YouTube quota budget")
    run.add_argument("--backend", default="ollama", help="LLM backend")

    # Strategy commands
    sub.add_parser("strategy-list", help="List all strategies")
    sub.add_parser("persona-list", help="List all personas and their status")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if args.command == "bootstrap":
        from app.db import Database
        from app.personas.sarcastic_ai.strategies import bootstrap_strategies, bootstrap_scoring
        with Database(args.db_path) as db:
            db.ensure_all_tables()
            count = bootstrap_strategies(db, persona_id="sarcastic_ai")
            bootstrap_scoring(db, persona_id="sarcastic_ai")
            print(f"Bootstrap complete: strategies_seeded={count}")

    elif args.command == "run":
        os.environ.setdefault("LLM_BACKEND", args.backend)
        from app.db import Database
        from app.personas import PersonaOrchestrator, ALL_PERSONAS
        from app.personas.protocol import RunContext

        db = Database(args.db_path)
        db.connect()
        db.ensure_all_tables()

        context = RunContext(
            dry_run=args.dry_run,
            no_review=args.no_review,
            go_url=args.go_url,
            quota_budget=args.quota_budget,
        )

        if args.persona:
            persona_map = {cls().persona_id: cls for cls in ALL_PERSONAS}
            if args.persona not in persona_map:
                print(f"Unknown persona: {args.persona}. Available: {', '.join(persona_map.keys())}")
                sys.exit(1)
            persona_classes = [persona_map[args.persona]]
        else:
            persona_classes = list(ALL_PERSONAS)

        orchestrator = PersonaOrchestrator(persona_classes=persona_classes)
        results = asyncio.run(orchestrator.run_all(db, context))

        for pid, r in results.items():
            print(f"[{pid}] discovered={r.videos_discovered} uploaded={r.videos_uploaded} rejected={r.videos_rejected} errors={len(r.errors)}")

        db.close()

    elif args.command == "strategy-list":
        from app.db import Database
        from app.personas import ALL_PERSONAS
        with Database(args.db_path) as db:
            db.ensure_all_tables()
            for cls in ALL_PERSONAS:
                pid = cls().persona_id
                strategies = db.list_strategies(persona_id=pid)
                if strategies:
                    print(f"\n  [{pid}] ({len(strategies)} strategies)")
                    for s in strategies:
                        print(f"    {s['name']} yield={s['yield_rate']:.0%} active={bool(s['is_active'])}")

    elif args.command == "persona-list":
        from app.personas import ALL_PERSONAS
        for cls in ALL_PERSONAS:
            p = cls()
            print(f"  {p.persona_id}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
