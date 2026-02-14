#!/usr/bin/env python3
"""
CLI for Bilibili Performance Tracker and Competitor Monitoring

Usage:
    python -m app.cli track --db-path /path/to/db.sqlite
    python -m app.cli label --db-path /path/to/db.sqlite
    python -m app.cli stats --db-path /path/to/db.sqlite
    python -m app.cli add-competitor --db-path /path/to/db.sqlite UID
    python -m app.cli list-competitors --db-path /path/to/db.sqlite
    python -m app.cli collect-competitor --db-path /path/to/db.sqlite UID
    python -m app.cli collect-all-competitors --db-path /path/to/db.sqlite
    python -m app.cli label-videos --db-path /path/to/db.sqlite
    python -m app.cli train --db-path /path/to/db.sqlite [--gpu] [--num-rounds 500]
    python -m app.cli discover --db-path /path/to/db.sqlite
    python -m app.cli discover-trending --db-path /path/to/db.sqlite
    python -m app.cli discover-history --db-path /path/to/db.sqlite
"""
import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime

from .db.database import Database, CompetitorChannel
from .collectors.bilibili_tracker import BilibiliTracker, CHECKPOINTS
from .collectors.competitor_monitor import CompetitorMonitor
from .collectors.labeler import Labeler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Bilibili Performance Tracker CLI"
    )
    parser.add_argument(
        "--db-path",
        required=True,
        help="Path to SQLite database (or PostgreSQL connection string)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Track command
    track_parser = subparsers.add_parser(
        "track",
        help="Collect performance metrics for uploads due at any checkpoint"
    )
    track_parser.add_argument(
        "--checkpoint",
        type=int,
        choices=CHECKPOINTS,
        help="Only track specific checkpoint (default: all due checkpoints)"
    )

    # Label command
    label_parser = subparsers.add_parser(
        "label",
        help="Auto-label uploads based on performance thresholds"
    )
    label_parser.add_argument(
        "--min-checkpoint",
        type=int,
        default=168,
        help="Minimum checkpoint hours required for labeling (default: 168 = 7 days)"
    )

    # Stats command
    stats_parser = subparsers.add_parser(
        "stats",
        help="Show upload statistics summary"
    )

    # Phase 3B: Competitor monitoring commands

    # Add competitor command
    add_competitor_parser = subparsers.add_parser(
        "add-competitor",
        help="Add a competitor channel to monitor"
    )
    add_competitor_parser.add_argument(
        "uid",
        help="Bilibili user ID (mid) of the competitor channel"
    )

    # List competitors command
    list_competitors_parser = subparsers.add_parser(
        "list-competitors",
        help="List tracked competitor channels"
    )

    # Collect competitor command
    collect_competitor_parser = subparsers.add_parser(
        "collect-competitor",
        help="Collect videos from a specific competitor channel"
    )
    collect_competitor_parser.add_argument(
        "uid",
        help="Bilibili user ID (mid) of the competitor channel"
    )
    collect_competitor_parser.add_argument(
        "--count",
        type=int,
        default=100,
        help="Number of videos to collect (default: 100)"
    )

    # Collect all competitors command
    collect_all_parser = subparsers.add_parser(
        "collect-all-competitors",
        help="Collect videos from all active competitor channels"
    )
    collect_all_parser.add_argument(
        "--count",
        type=int,
        default=100,
        help="Number of videos per channel (default: 100)"
    )

    # Label videos command
    label_videos_parser = subparsers.add_parser(
        "label-videos",
        help="Auto-label competitor videos based on performance thresholds"
    )
    label_videos_parser.add_argument(
        "--relabel",
        action="store_true",
        help="Relabel all videos including already labeled ones"
    )
    label_videos_parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="Maximum number of videos to label (default: 1000)"
    )

    # Training data status command
    training_status_parser = subparsers.add_parser(
        "training-status",
        help="Show training data summary by label"
    )

    # Train model command
    train_parser = subparsers.add_parser(
        "train",
        help="Train LightGBM scoring model on labeled competitor videos"
    )
    train_parser.add_argument(
        "--model-dir",
        default="models",
        help="Directory to save model artifacts (default: models)"
    )
    train_parser.add_argument(
        "--test-size",
        type=float,
        default=0.2,
        help="Fraction of data for test set (default: 0.2)"
    )
    train_parser.add_argument(
        "--num-rounds",
        type=int,
        default=500,
        help="Max boosting rounds (default: 500)"
    )
    train_parser.add_argument(
        "--learning-rate",
        type=float,
        default=None,
        help="Learning rate (default: 0.05)"
    )
    train_parser.add_argument(
        "--min-samples",
        type=int,
        default=50,
        help="Minimum required labeled samples (default: 50)"
    )
    train_parser.add_argument(
        "--gpu",
        action="store_true",
        default=None,
        help="Force GPU training"
    )
    train_parser.add_argument(
        "--no-gpu",
        action="store_true",
        help="Force CPU training"
    )

    # Discovery pipeline commands

    discover_parser = subparsers.add_parser(
        "discover",
        help="Run the hot words discovery pipeline"
    )
    discover_parser.add_argument(
        "--max-keywords",
        type=int,
        default=10,
        help="Max trending keywords to process (default: 10)"
    )
    discover_parser.add_argument(
        "--videos-per-keyword",
        type=int,
        default=5,
        help="Max YouTube videos per keyword (default: 5)"
    )
    discover_parser.add_argument(
        "--model-dir",
        default="models",
        help="Directory with trained model (default: models)"
    )
    discover_parser.add_argument(
        "--llm-model",
        default="qwen2.5:7b",
        help="Ollama model for relevance scoring (default: qwen2.5:7b)"
    )

    discover_trending_parser = subparsers.add_parser(
        "discover-trending",
        help="Fetch and display current Bilibili trending keywords"
    )

    discover_history_parser = subparsers.add_parser(
        "discover-history",
        help="Show past discovery run results"
    )
    discover_history_parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Number of recent runs to show (default: 5)"
    )

    return parser.parse_args()


async def cmd_track(db: Database, args) -> dict:
    """Execute the track command."""
    tracker = BilibiliTracker(db)

    if args.checkpoint:
        # Track specific checkpoint
        uploads = db.get_uploads_for_tracking(args.checkpoint)
        count = 0
        for upload in uploads:
            try:
                perf = await tracker.collect_metrics(upload, args.checkpoint)
                if perf:
                    count += 1
            except Exception as e:
                logger.error(f"Error tracking {upload.bilibili_bvid}: {e}")

        return {
            "command": "track",
            "checkpoint": args.checkpoint,
            "tracked": count
        }
    else:
        # Track all due checkpoints
        results = await tracker.track_all_due()
        total = sum(results.values())
        return {
            "command": "track",
            "by_checkpoint": results,
            "total_tracked": total
        }


async def cmd_label(db: Database, args) -> dict:
    """Execute the label command."""
    tracker = BilibiliTracker(db)
    count = await tracker.label_all_due(args.min_checkpoint)
    return {
        "command": "label",
        "min_checkpoint": args.min_checkpoint,
        "labeled": count
    }


async def cmd_add_competitor(db: Database, args) -> dict:
    """Execute the add-competitor command."""
    db.ensure_competitor_tables()

    monitor = CompetitorMonitor(db)
    channel = await monitor.get_channel_info(args.uid)

    if channel is None:
        return {
            "command": "add-competitor",
            "success": False,
            "error": f"Could not find channel with UID {args.uid}"
        }

    db.add_competitor_channel(channel)

    return {
        "command": "add-competitor",
        "success": True,
        "channel": {
            "uid": channel.bilibili_uid,
            "name": channel.name,
            "followers": channel.follower_count
        }
    }


def cmd_list_competitors(db: Database, args) -> dict:
    """Execute the list-competitors command."""
    db.ensure_competitor_tables()

    channels = db.list_competitor_channels(active_only=True)

    return {
        "command": "list-competitors",
        "count": len(channels),
        "channels": [
            {
                "uid": ch.bilibili_uid,
                "name": ch.name,
                "followers": ch.follower_count,
                "videos": ch.video_count,
                "added": ch.added_at.isoformat()
            }
            for ch in channels
        ]
    }


async def cmd_collect_competitor(db: Database, args) -> dict:
    """Execute the collect-competitor command."""
    db.ensure_competitor_tables()

    monitor = CompetitorMonitor(db)
    collected, with_youtube = await monitor.collect_channel(args.uid, args.count)

    return {
        "command": "collect-competitor",
        "uid": args.uid,
        "videos_collected": collected,
        "with_youtube_source": with_youtube
    }


async def cmd_collect_all_competitors(db: Database, args) -> dict:
    """Execute the collect-all-competitors command."""
    db.ensure_competitor_tables()

    monitor = CompetitorMonitor(db)
    results = await monitor.collect_all_active(args.count)

    return {
        "command": "collect-all-competitors",
        **results
    }


def cmd_label_videos(db: Database, args) -> dict:
    """Execute the label-videos command."""
    db.ensure_competitor_tables()

    labeler = Labeler(db)

    if args.relabel:
        results = labeler.relabel_all(limit=args.limit)
    else:
        results = labeler.label_all_unlabeled(limit=args.limit)

    return {
        "command": "label-videos",
        "relabel": args.relabel,
        **results
    }


def cmd_training_status(db: Database, args) -> dict:
    """Execute the training-status command."""
    db.ensure_competitor_tables()

    summary = db.get_training_data_summary()

    return {
        "command": "training-status",
        **summary
    }


def cmd_train(db: Database, args) -> dict:
    """Execute the train command (sync — CPU/GPU bound)."""
    # Lazy imports to avoid loading ML deps for other commands
    from .training.trainer import train_model

    db.ensure_competitor_tables()

    # Resolve GPU flag
    use_gpu = None
    if args.gpu:
        use_gpu = True
    elif args.no_gpu:
        use_gpu = False

    model, report, validation = train_model(
        db,
        model_dir=args.model_dir,
        test_size=args.test_size,
        num_rounds=args.num_rounds,
        learning_rate=args.learning_rate,
        min_samples=args.min_samples,
        use_gpu=use_gpu,
    )

    result = {
        "command": "train",
        "validation": {
            "is_valid": validation.is_valid,
            "total_samples": validation.total_samples,
            "class_distribution": validation.class_distribution,
            "warnings": validation.warnings,
            "errors": validation.errors,
        },
    }

    if model and report:
        result["success"] = True
        result["evaluation"] = {
            "accuracy": round(report.accuracy, 4),
            "weighted_f1": round(report.weighted_f1, 4),
            "macro_f1": round(report.macro_f1, 4),
            "logloss": round(report.logloss, 4),
        }
        result["model_dir"] = args.model_dir
    else:
        result["success"] = False

    return result


async def cmd_discover(db: Database, args) -> dict:
    """Execute the discover command — full pipeline run."""
    from .discovery.pipeline import DiscoveryPipeline

    db.ensure_discovery_tables()

    pipeline = DiscoveryPipeline(
        db, model_dir=args.model_dir, llm_model=args.llm_model,
    )
    recommendations = await pipeline.run(
        max_keywords=args.max_keywords,
        videos_per_keyword=args.videos_per_keyword,
    )

    return {
        "command": "discover",
        "total_recommendations": len(recommendations),
        "recommendations": [
            {
                "keyword": r.keyword,
                "heat_score": r.heat_score,
                "youtube_video_id": r.youtube_video_id,
                "youtube_title": r.youtube_title,
                "youtube_channel": r.youtube_channel,
                "youtube_views": r.youtube_views,
                "relevance_score": round(r.relevance_score, 3),
                "predicted_views": round(r.predicted_views, 0) if r.predicted_views else None,
                "predicted_label": r.predicted_label,
                "combined_score": round(r.combined_score, 4),
            }
            for r in recommendations
        ],
    }


async def cmd_discover_trending(db: Database, args) -> dict:
    """Execute the discover-trending command."""
    from .discovery.trending import fetch_trending_keywords

    keywords = await fetch_trending_keywords()
    return {
        "command": "discover-trending",
        "count": len(keywords),
        "keywords": [
            {
                "keyword": kw.keyword,
                "heat_score": kw.heat_score,
                "position": kw.position,
            }
            for kw in keywords
        ],
    }


def cmd_discover_history(db: Database, args) -> dict:
    """Execute the discover-history command."""
    db.ensure_discovery_tables()
    history = db.get_discovery_history(limit=args.limit)
    return {
        "command": "discover-history",
        "runs": history,
    }


def cmd_stats(db: Database, args) -> dict:
    """Execute the stats command."""
    # Get basic counts
    uploads = db.get_all_uploads_with_bvid()
    total_with_bvid = len(uploads)

    # Get labels
    labels = {"viral": 0, "successful": 0, "standard": 0, "failed": 0, "unlabeled": 0}

    for upload in uploads:
        perf = db.get_latest_performance(upload.video_id)
        if perf:
            # Check if labeled
            cursor = db._conn.execute(
                "SELECT label FROM upload_outcomes WHERE upload_id = ?",
                (upload.video_id,)
            )
            row = cursor.fetchone()
            if row:
                labels[row["label"]] = labels.get(row["label"], 0) + 1
            else:
                labels["unlabeled"] += 1

    # Get average metrics
    cursor = db._conn.execute("""
        SELECT
            AVG(views) as avg_views,
            AVG(likes) as avg_likes,
            AVG(coins) as avg_coins,
            AVG(engagement_rate) as avg_engagement
        FROM upload_performance
        WHERE (upload_id, checkpoint_hours) IN (
            SELECT upload_id, MAX(checkpoint_hours)
            FROM upload_performance
            GROUP BY upload_id
        )
    """)
    row = cursor.fetchone()
    avg_views = row["avg_views"] or 0
    avg_likes = row["avg_likes"] or 0
    avg_coins = row["avg_coins"] or 0
    avg_engagement = row["avg_engagement"] or 0

    return {
        "command": "stats",
        "total_uploads_with_bvid": total_with_bvid,
        "by_label": labels,
        "averages": {
            "views": round(avg_views, 2),
            "likes": round(avg_likes, 2),
            "coins": round(avg_coins, 2),
            "engagement_rate": round(avg_engagement * 100, 2)
        }
    }


async def main():
    """Main entry point."""
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

    args = parse_args()

    with Database(args.db_path) as db:
        if args.command == "track":
            result = await cmd_track(db, args)
        elif args.command == "label":
            result = await cmd_label(db, args)
        elif args.command == "stats":
            result = cmd_stats(db, args)
        elif args.command == "add-competitor":
            result = await cmd_add_competitor(db, args)
        elif args.command == "list-competitors":
            result = cmd_list_competitors(db, args)
        elif args.command == "collect-competitor":
            result = await cmd_collect_competitor(db, args)
        elif args.command == "collect-all-competitors":
            result = await cmd_collect_all_competitors(db, args)
        elif args.command == "label-videos":
            result = cmd_label_videos(db, args)
        elif args.command == "training-status":
            result = cmd_training_status(db, args)
        elif args.command == "train":
            result = cmd_train(db, args)
        elif args.command == "discover":
            result = await cmd_discover(db, args)
        elif args.command == "discover-trending":
            result = await cmd_discover_trending(db, args)
        elif args.command == "discover-history":
            result = cmd_discover_history(db, args)
        else:
            logger.error(f"Unknown command: {args.command}")
            sys.exit(1)

    # Output results
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"\n{'=' * 50}")
        print(f"Command: {result['command']}")
        print(f"{'=' * 50}")

        if args.command == "track":
            if "checkpoint" in result:
                print(f"Checkpoint: {result['checkpoint']}h")
                print(f"Tracked: {result['tracked']} uploads")
            else:
                print("By checkpoint:")
                for cp, count in result.get("by_checkpoint", {}).items():
                    print(f"  {cp}: {count} uploads")
                print(f"Total tracked: {result['total_tracked']}")

        elif args.command == "label":
            print(f"Min checkpoint: {result['min_checkpoint']}h")
            print(f"Labeled: {result['labeled']} uploads")

        elif args.command == "stats":
            print(f"Total uploads with bvid: {result['total_uploads_with_bvid']}")
            print("\nBy label:")
            for label, count in result["by_label"].items():
                print(f"  {label}: {count}")
            print("\nAverages (from latest checkpoint):")
            avgs = result["averages"]
            print(f"  Views: {avgs['views']:,.0f}")
            print(f"  Likes: {avgs['likes']:,.0f}")
            print(f"  Coins: {avgs['coins']:,.0f}")
            print(f"  Engagement rate: {avgs['engagement_rate']}%")

        elif args.command == "add-competitor":
            if result.get("success"):
                ch = result["channel"]
                print(f"Added competitor channel:")
                print(f"  UID: {ch['uid']}")
                print(f"  Name: {ch['name']}")
                print(f"  Followers: {ch['followers']:,}")
            else:
                print(f"Error: {result.get('error', 'Unknown error')}")

        elif args.command == "list-competitors":
            print(f"Active competitor channels: {result['count']}")
            if result["channels"]:
                print("\n  UID          | NAME                     | FOLLOWERS  | VIDEOS")
                print("  " + "-" * 65)
                for ch in result["channels"]:
                    print(f"  {ch['uid']:<12} | {ch['name'][:24]:<24} | {ch['followers']:>10,} | {ch['videos']:>6}")

        elif args.command == "collect-competitor":
            print(f"Collected from channel: {result['uid']}")
            print(f"  Videos collected: {result['videos_collected']}")
            print(f"  With YouTube source: {result['with_youtube_source']}")

        elif args.command == "collect-all-competitors":
            print(f"Channels processed: {result['channels_processed']}")
            print(f"Total videos: {result['total_videos']}")
            print(f"With YouTube source: {result['with_youtube_source']}")
            if result.get('errors', 0) > 0:
                print(f"Errors: {result['errors']}")

        elif args.command == "label-videos":
            print(f"Mode: {'Relabel all' if result.get('relabel') else 'Label unlabeled only'}")
            print(f"Total processed: {result['total']}")
            print(f"\nBy label:")
            print(f"  viral: {result.get('viral', 0)}")
            print(f"  successful: {result.get('successful', 0)}")
            print(f"  standard: {result.get('standard', 0)}")
            print(f"  failed: {result.get('failed', 0)}")
            if result.get('unchanged', 0) > 0:
                print(f"  unchanged: {result['unchanged']}")
            if result.get('errors', 0) > 0:
                print(f"Errors: {result['errors']}")

        elif args.command == "training-status":
            print(f"Training Data Summary:")
            print(f"  Total videos: {result.get('total', 0)}")
            print(f"\nBy label:")
            print(f"  viral: {result.get('viral', 0)}")
            print(f"  successful: {result.get('successful', 0)}")
            print(f"  standard: {result.get('standard', 0)}")
            print(f"  failed: {result.get('failed', 0)}")
            print(f"  unlabeled: {result.get('unlabeled', 0)}")

        elif args.command == "train":
            v = result["validation"]
            print(f"Data: {v['total_samples']} labeled samples")
            if v["class_distribution"]:
                for name, count in sorted(v["class_distribution"].items()):
                    print(f"  {name}: {count}")
            if v["errors"]:
                print(f"\nTraining FAILED — data requirements not met:")
                for e in v["errors"]:
                    print(f"  - {e}")
            if v["warnings"]:
                print(f"\nWarnings:")
                for w in v["warnings"]:
                    print(f"  - {w}")
            if result.get("success"):
                ev = result["evaluation"]
                print(f"\nModel trained successfully!")
                print(f"  Accuracy:    {ev['accuracy']}")
                print(f"  Weighted F1: {ev['weighted_f1']}")
                print(f"  Macro F1:    {ev['macro_f1']}")
                print(f"  Log Loss:    {ev['logloss']}")
                print(f"  Saved to:    {result['model_dir']}/")

        elif args.command == "discover":
            print(f"Recommendations: {result['total_recommendations']}")
            for i, rec in enumerate(result.get("recommendations", [])[:20], 1):
                label = rec.get("predicted_label") or "n/a"
                pv = rec.get("predicted_views")
                pv_str = f"{pv:,.0f}" if pv else "n/a"
                print(f"\n  #{i} [{rec['combined_score']:.4f}] {rec['youtube_title'][:60]}")
                print(f"     Keyword: {rec['keyword']} (heat={rec['heat_score']:,})")
                print(f"     Channel: {rec['youtube_channel']}")
                print(f"     YT views: {rec['youtube_views']:,} | Relevance: {rec['relevance_score']:.2f}")
                print(f"     Predicted: {pv_str} views ({label})")

        elif args.command == "discover-trending":
            print(f"Trending keywords: {result['count']}")
            for kw in result.get("keywords", []):
                print(f"  #{kw['position']:>2} [{kw['heat_score']:>10,}] {kw['keyword']}")

        elif args.command == "discover-history":
            runs = result.get("runs", [])
            if not runs:
                print("No discovery runs found.")
            for run in runs:
                print(f"\n  Run #{run['run_id']} at {run['run_at']}")
                print(f"    Keywords: {run['keywords_fetched']}, "
                      f"Candidates: {run['candidates_found']}, "
                      f"Recommendations: {run['recommendations_count']}")
                for rec in run.get("top_recommendations", [])[:5]:
                    print(f"    [{rec['combined_score']:.4f}] {rec['youtube_title'][:50]} "
                          f"({rec['keyword']})")

        print(f"{'=' * 50}\n")


if __name__ == "__main__":
    asyncio.run(main())
