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
    python -m app.cli train-predictor --db-path /path/to/db.sqlite
    python -m app.cli fine-tune --db-path /path/to/db.sqlite
"""
import argparse
import asyncio
import json
import logging
import os
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
    train_parser.add_argument(
        "--no-random-intercepts",
        action="store_true",
        help="Use pure LightGBM (no per-channel random intercepts, better for unseen channels)"
    )

    # Discovery pipeline commands

    discover_parser = subparsers.add_parser(
        "discover",
        help="Run the strategy-driven discovery pipeline"
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
        help="Ollama model for scoring (default: qwen2.5:7b)"
    )
    discover_parser.add_argument(
        "--max-age-days",
        type=int,
        default=30,
        help="Only consider YouTube videos published within N days (default: 30, 0=no limit)"
    )
    discover_parser.add_argument(
        "--backend",
        default="ollama",
        choices=["ollama", "openai", "anthropic"],
        help="LLM backend for scoring and prediction (default: ollama)"
    )

    discover_trending_parser = subparsers.add_parser(
        "discover-trending",
        help="Fetch and display current Bilibili trending keywords"
    )

    # Fine-tune embeddings command
    finetune_parser = subparsers.add_parser(
        "fine-tune-embeddings",
        help="Fine-tune title embedder and build RAG vector store"
    )
    finetune_parser.add_argument(
        "--model-dir",
        default="models",
        help="Directory to save embedder and vector store (default: models)"
    )
    finetune_parser.add_argument(
        "--epochs",
        type=int,
        default=30,
        help="Max training epochs (default: 30)"
    )
    finetune_parser.add_argument(
        "--batch-size",
        type=int,
        default=64,
        help="Batch size (default: 64)"
    )
    finetune_parser.add_argument(
        "--learning-rate",
        type=float,
        default=1e-3,
        help="Learning rate (default: 1e-3)"
    )
    finetune_parser.add_argument(
        "--projection-dim",
        type=int,
        default=128,
        help="Embedding projection dimension (default: 128)"
    )
    finetune_parser.add_argument(
        "--full-finetune",
        action="store_true",
        help="Unfreeze transformer backbone (default: frozen)"
    )
    finetune_parser.add_argument(
        "--backbone-lr",
        type=float,
        default=2e-5,
        help="Backbone learning rate for full fine-tune (default: 2e-5)"
    )
    finetune_parser.add_argument(
        "--patience",
        type=int,
        default=5,
        help="Early stopping patience (default: 5)"
    )

    # Train neural predictor command
    predictor_parser = subparsers.add_parser(
        "train-predictor",
        help="Train the PyTorch neural predictor model"
    )
    predictor_parser.add_argument(
        "--model-dir", default="models",
        help="Directory with embedder/vector store and to save predictor (default: models)"
    )
    predictor_parser.add_argument(
        "--epochs", type=int, default=50,
        help="Max training epochs (default: 50)"
    )
    predictor_parser.add_argument(
        "--batch-size", type=int, default=256,
        help="Batch size (default: 256)"
    )
    predictor_parser.add_argument(
        "--learning-rate", type=float, default=1e-3,
        help="Learning rate (default: 1e-3)"
    )
    predictor_parser.add_argument(
        "--patience", type=int, default=8,
        help="Early stopping patience (default: 8)"
    )

    # Predict command
    predict_parser = subparsers.add_parser(
        "predict",
        help="Test prediction on a single YouTube video URL or ID"
    )
    predict_parser.add_argument(
        "video",
        help="YouTube video URL or video ID"
    )
    predict_parser.add_argument(
        "--model-dir", default="models",
        help="Directory with trained models (default: models)"
    )
    predict_parser.add_argument(
        "--backend", default="ollama",
        choices=["ollama", "openai", "anthropic"],
        help="LLM backend for prediction (default: ollama)"
    )
    predict_parser.add_argument(
        "--llm-model", default="qwen2.5:7b",
        help="LLM model name (default: qwen2.5:7b)"
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

    # Fine-tune LLM command (LoRA)
    ft_llm_parser = subparsers.add_parser(
        "fine-tune",
        help="Fine-tune Qwen 2.5 7B with LoRA on transport data"
    )
    ft_llm_parser.add_argument(
        "--model-dir", default="models",
        help="Directory with data and to save artifacts (default: models)"
    )
    ft_llm_parser.add_argument(
        "--base-model", default="Qwen/Qwen2.5-7B",
        help="HuggingFace base model (default: Qwen/Qwen2.5-7B)"
    )
    ft_llm_parser.add_argument(
        "--output-dir", default="models/finetuned",
        help="Directory for fine-tuned model output (default: models/finetuned)"
    )
    ft_llm_parser.add_argument(
        "--ollama-name", default="transport-qwen",
        help="Ollama model name (default: transport-qwen)"
    )
    ft_llm_parser.add_argument(
        "--prepare-only", action="store_true",
        help="Only prepare training data, don't train"
    )
    ft_llm_parser.add_argument(
        "--export-only", action="store_true",
        help="Only export existing adapter to Ollama"
    )

    # ── Skill-Based Discovery Framework Commands ──

    bootstrap_parser = subparsers.add_parser(
        "bootstrap",
        help="One-time setup: seed strategies, channels, scoring params"
    )
    bootstrap_parser.add_argument(
        "--skip-llm", action="store_true",
        help="Skip LLM calls (use default principles)"
    )
    bootstrap_parser.add_argument(
        "--backend", default="ollama",
        choices=["ollama", "openai", "anthropic"],
        help="LLM backend for principle generation (default: ollama)"
    )

    strategy_list_parser = subparsers.add_parser(
        "strategy-list",
        help="Show strategies with yield rates and transport stats"
    )

    strategy_add_parser = subparsers.add_parser(
        "strategy-add",
        help="Manually add a new discovery strategy"
    )
    strategy_add_parser.add_argument("name", help="Strategy name (snake_case)")
    strategy_add_parser.add_argument("description", help="Strategy description")
    strategy_add_parser.add_argument(
        "--bilibili-check", default="",
        help="Chinese search term for saturation check"
    )

    follow_channel_parser = subparsers.add_parser(
        "follow-channel",
        help="Add a YouTube channel to follow"
    )
    follow_channel_parser.add_argument("channel_name", help="YouTube channel name")
    follow_channel_parser.add_argument(
        "--reason", default="",
        help="Why we follow this channel"
    )

    skill_show_parser = subparsers.add_parser(
        "skill-show",
        help="Show a skill's current prompt and version"
    )
    skill_show_parser.add_argument("skill_name", help="Skill name (e.g. strategy_generation)")

    skill_history_parser = subparsers.add_parser(
        "skill-history",
        help="Show prompt evolution history for a skill"
    )
    skill_history_parser.add_argument("skill_name", help="Skill name")
    skill_history_parser.add_argument(
        "--limit", type=int, default=10,
        help="Number of versions to show (default: 10)"
    )

    skill_rollback_parser = subparsers.add_parser(
        "skill-rollback",
        help="Roll back a skill prompt to a previous version"
    )
    skill_rollback_parser.add_argument("skill_name", help="Skill name")
    skill_rollback_parser.add_argument("version", type=int, help="Target version number")

    return parser.parse_args()


# ── Skill-Based Discovery Framework Handlers ──


def cmd_bootstrap(db: Database, args) -> dict:
    """Execute the bootstrap command."""
    from .bootstrap import run_bootstrap

    backend = None
    if not args.skip_llm:
        from .llm.backend import create_backend
        try:
            backend = create_backend(args.backend)
        except Exception as e:
            logger.warning("Could not create LLM backend: %s", e)

    result = run_bootstrap(db, backend=backend, skip_llm=args.skip_llm)
    return {"command": "bootstrap", **result}


def cmd_strategy_list(db: Database, args) -> dict:
    """Execute the strategy-list command."""
    db.ensure_skill_tables()
    strategies = db.list_strategies(active_only=True)
    return {
        "command": "strategy-list",
        "count": len(strategies),
        "strategies": strategies,
    }


def cmd_strategy_add(db: Database, args) -> dict:
    """Execute the strategy-add command."""
    db.ensure_skill_tables()
    strategy_id = db.add_strategy(
        name=args.name,
        description=args.description,
        bilibili_check=args.bilibili_check or None,
        source="manual",
    )
    return {
        "command": "strategy-add",
        "success": True,
        "strategy_id": strategy_id,
        "name": args.name,
    }


def cmd_follow_channel(db: Database, args) -> dict:
    """Execute the follow-channel command."""
    db.ensure_skill_tables()
    channel_id = db.add_followed_channel(
        channel_name=args.channel_name,
        reason=args.reason or None,
        source="manual",
    )
    return {
        "command": "follow-channel",
        "success": True,
        "channel_name": args.channel_name,
    }


def cmd_skill_show(db: Database, args) -> dict:
    """Execute the skill-show command."""
    db.ensure_skill_tables()
    skill = db.get_skill(args.skill_name)
    if not skill:
        return {
            "command": "skill-show",
            "error": f"Skill '{args.skill_name}' not found",
        }
    return {
        "command": "skill-show",
        "name": skill["name"],
        "version": skill["version"],
        "system_prompt": skill["system_prompt"],
        "prompt_template": skill["prompt_template"],
        "updated_at": skill["updated_at"],
    }


def cmd_skill_history(db: Database, args) -> dict:
    """Execute the skill-history command."""
    db.ensure_skill_tables()
    versions = db.get_skill_versions(args.skill_name)
    return {
        "command": "skill-history",
        "skill_name": args.skill_name,
        "versions": versions[:args.limit],
    }


def cmd_skill_rollback(db: Database, args) -> dict:
    """Execute the skill-rollback command."""
    db.ensure_skill_tables()

    # Need a dummy backend for the Skill class
    from unittest.mock import MagicMock
    from .skills.base import Skill

    class RollbackSkill(Skill):
        def _default_system_prompt(self):
            return ""
        def _default_prompt_template(self):
            return ""
        def _output_schema(self):
            return {"type": "object"}

    skill = RollbackSkill(args.skill_name, db, MagicMock())
    success = skill.rollback(args.version)
    return {
        "command": "skill-rollback",
        "success": success,
        "skill_name": args.skill_name,
        "target_version": args.version,
    }


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

    use_ri = not args.no_random_intercepts

    model, report, metadata = train_model(
        db,
        model_dir=args.model_dir,
        num_rounds=args.num_rounds,
        learning_rate=args.learning_rate,
        min_samples=args.min_samples,
        use_random_intercepts=use_ri,
    )

    result = {"command": "train"}

    if model and report:
        result["success"] = True
        result["evaluation"] = {
            "rmse": round(report.rmse, 4),
            "r2": round(report.r2, 4),
            "mae": round(report.mae, 4),
            "correlation": round(report.correlation, 4),
        }
        result["model_dir"] = args.model_dir
        result["metadata"] = {
            "training_samples": metadata.get("training_samples"),
            "unique_channels": metadata.get("unique_channels"),
            "cv_mean_r2": metadata.get("cv_evaluation", {}).get("mean_r2"),
            "cv_mean_correlation": metadata.get("cv_evaluation", {}).get("mean_correlation"),
        }
    else:
        result["success"] = False
        result["error"] = metadata.get("error", "Training failed")

    return result


async def cmd_discover(db: Database, args) -> dict:
    """Execute the discover command — full pipeline run."""
    from .discovery.pipeline import DiscoveryPipeline

    db.ensure_discovery_tables()

    pipeline = DiscoveryPipeline(
        db, model_dir=args.model_dir, llm_model=args.llm_model,
        backend_type=getattr(args, "backend", "ollama"),
    )
    recommendations = await pipeline.run(
        max_keywords=args.max_keywords,
        videos_per_keyword=args.videos_per_keyword,
        max_age_days=args.max_age_days,
    )

    return {
        "command": "discover",
        "total_recommendations": len(recommendations),
        "recommendations": [
            {
                "strategy": r.strategy,
                "query_used": r.query_used,
                "youtube_video_id": r.youtube_video_id,
                "youtube_title": r.youtube_title,
                "youtube_channel": r.youtube_channel,
                "youtube_views": r.youtube_views,
                "novelty_score": round(r.novelty_score, 3),
                "predicted_views": round(r.predicted_views, 0) if r.predicted_views else None,
                "predicted_label": r.predicted_label,
                "confidence": round(r.confidence, 3),
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


def cmd_fine_tune_embeddings(db: Database, args) -> dict:
    """Execute the fine-tune-embeddings command."""
    from .embeddings.trainer import fine_tune_embeddings

    db.ensure_competitor_tables()

    embedder, metrics = fine_tune_embeddings(
        db,
        model_dir=args.model_dir,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.learning_rate,
        patience=args.patience,
        freeze_backbone=not args.full_finetune,
        projection_dim=args.projection_dim,
        backbone_lr=args.backbone_lr,
    )

    result = {"command": "fine-tune-embeddings"}
    if embedder is not None and metrics is not None:
        result["success"] = True
        result["metrics"] = metrics
    else:
        result["success"] = False
        result["error"] = "Insufficient data or training failed"

    return result


def cmd_train_predictor(db: Database, args) -> dict:
    """Execute the train-predictor command."""
    from .prediction.trainer import train_predictor

    db.ensure_competitor_tables()

    model, metrics = train_predictor(
        db,
        model_dir=args.model_dir,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.learning_rate,
        patience=args.patience,
    )

    result = {"command": "train-predictor"}
    if model is not None and metrics is not None:
        result["success"] = True
        result["metrics"] = metrics
    else:
        result["success"] = False
        result["error"] = "Training failed — insufficient data or error"

    return result


def cmd_fine_tune(db: Database, args) -> dict:
    """Execute the fine-tune command (LoRA on Qwen 2.5 7B)."""
    from .finetuning.prepare_data import prepare_training_data

    db.ensure_competitor_tables()

    result = {"command": "fine-tune"}

    # Step 1: Prepare data
    try:
        train_path, val_path, stats = prepare_training_data(
            db, model_dir=args.model_dir,
        )
        result["data_stats"] = stats
    except Exception as e:
        result["success"] = False
        result["error"] = f"Data preparation failed: {e}"
        return result

    if args.prepare_only:
        result["success"] = True
        result["phase"] = "data_prepared"
        return result

    if args.export_only:
        # Skip training, just export
        adapter_path = os.path.join(args.output_dir, "lora_adapter")
        if not os.path.exists(adapter_path):
            result["success"] = False
            result["error"] = f"No adapter found at {adapter_path}"
            return result

        from .finetuning.export_model import export_full_pipeline
        try:
            export_result = export_full_pipeline(
                adapter_path=adapter_path,
                base_model=args.base_model,
                output_dir=args.output_dir,
                model_name=args.ollama_name,
            )
            result["success"] = export_result["success"]
            result["export"] = export_result
        except Exception as e:
            result["success"] = False
            result["error"] = f"Export failed: {e}"
        return result

    # Step 2: Train LoRA
    try:
        from .finetuning.train_lora import train_lora
        train_result = train_lora(
            train_path=train_path,
            val_path=val_path,
            output_dir=args.output_dir,
            base_model=args.base_model,
        )
        result["training"] = train_result
    except ImportError as e:
        result["success"] = False
        result["error"] = f"Missing dependencies: {e}"
        return result
    except Exception as e:
        result["success"] = False
        result["error"] = f"Training failed: {e}"
        return result

    # Step 3: Export to Ollama
    try:
        from .finetuning.export_model import export_full_pipeline
        export_result = export_full_pipeline(
            adapter_path=train_result["adapter_path"],
            base_model=args.base_model,
            output_dir=args.output_dir,
            model_name=args.ollama_name,
        )
        result["export"] = export_result
        result["success"] = export_result["success"]
    except Exception as e:
        result["success"] = True  # Training succeeded even if export failed
        result["export_error"] = str(e)
        logger.warning("Export to Ollama failed (training succeeded): %s", e)

    return result


async def cmd_predict(db: Database, args) -> dict:
    """Execute the predict command — single video prediction."""
    import math
    import re

    # Parse video ID from URL or raw ID
    video_input = args.video
    match = re.search(r"(?:v=|youtu\.be/)([\w-]{11})", video_input)
    video_id = match.group(1) if match else video_input

    # Fetch video info from YouTube
    import httpx
    api_key = os.environ.get("YOUTUBE_API_KEY", "AIzaSyAvCrdRnFYXwya6MIEdcN9jv4V-SxFYu1U")
    client = httpx.Client()
    try:
        resp = client.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={
                "part": "snippet,statistics,contentDetails",
                "id": video_id,
                "key": api_key,
            },
            timeout=30,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
    finally:
        client.close()

    if not items:
        return {"command": "predict", "error": f"Video not found: {video_id}"}

    item = items[0]
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    content = item.get("contentDetails", {})

    title = snippet.get("title", "")
    channel = snippet.get("channelTitle", "")
    yt_views = int(stats.get("viewCount", 0))
    yt_likes = int(stats.get("likeCount", 0))
    yt_comments = int(stats.get("commentCount", 0))
    category_id = int(snippet.get("categoryId", 0))

    # Parse duration
    dur_str = content.get("duration", "")
    dur_match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", dur_str or "")
    duration = 0
    if dur_match:
        duration = (int(dur_match.group(1) or 0) * 3600
                    + int(dur_match.group(2) or 0) * 60
                    + int(dur_match.group(3) or 0))

    result = {
        "command": "predict",
        "video_id": video_id,
        "title": title,
        "channel": channel,
        "yt_views": yt_views,
        "predictions": {},
    }

    # LLM prediction with evidence context
    try:
        from .prediction.llm_predictor import LLMPredictor
        predictor = LLMPredictor(
            backend_type=args.backend, model=args.llm_model,
        )
        llm_pred = predictor.predict(
            title=title, channel=channel, yt_views=yt_views,
            yt_likes=yt_likes, yt_comments=yt_comments,
            duration_seconds=duration, category_id=category_id,
        )
        if llm_pred:
            result["predictions"]["llm"] = {
                "log_views": round(llm_pred["predicted_log_views"], 3),
                "views": llm_pred["predicted_views"],
                "confidence": round(llm_pred["confidence"], 3),
                "label": llm_pred["label"],
                "reasoning": llm_pred["reasoning"],
            }
    except Exception as e:
        logger.warning("LLM prediction failed: %s", e)

    # Neural predictor
    predictor_path = os.path.join(args.model_dir, "predictor.pt")
    if not os.path.exists(predictor_path):
        predictor_path = os.path.join(args.model_dir, "reranker.pt")
    if os.path.exists(predictor_path):
        try:
            from .prediction.neural_reranker import NeuralPredictor
            nn_model = NeuralPredictor.load(predictor_path)
            result["predictions"]["neural_predictor"] = {"available": True, "path": predictor_path}
        except Exception as e:
            logger.warning("Neural predictor load failed: %s", e)

    return result


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
        elif args.command == "fine-tune-embeddings":
            result = cmd_fine_tune_embeddings(db, args)
        elif args.command == "train-predictor":
            result = cmd_train_predictor(db, args)
        elif args.command == "predict":
            result = await cmd_predict(db, args)
        elif args.command == "discover":
            result = await cmd_discover(db, args)
        elif args.command == "discover-trending":
            result = await cmd_discover_trending(db, args)
        elif args.command == "discover-history":
            result = cmd_discover_history(db, args)
        elif args.command == "fine-tune":
            result = cmd_fine_tune(db, args)
        elif args.command == "bootstrap":
            result = cmd_bootstrap(db, args)
        elif args.command == "strategy-list":
            result = cmd_strategy_list(db, args)
        elif args.command == "strategy-add":
            result = cmd_strategy_add(db, args)
        elif args.command == "follow-channel":
            result = cmd_follow_channel(db, args)
        elif args.command == "skill-show":
            result = cmd_skill_show(db, args)
        elif args.command == "skill-history":
            result = cmd_skill_history(db, args)
        elif args.command == "skill-rollback":
            result = cmd_skill_rollback(db, args)
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
            if result.get("success"):
                ev = result["evaluation"]
                m = result.get("metadata", {})
                print(f"Model trained successfully!")
                print(f"  Samples: {m.get('training_samples', '?')}, Channels: {m.get('unique_channels', '?')}")
                print(f"\n  Train metrics:")
                print(f"    RMSE:        {ev['rmse']}")
                print(f"    R2:          {ev['r2']}")
                print(f"    MAE:         {ev['mae']}")
                print(f"    Correlation: {ev['correlation']}")
                cv_r2 = m.get('cv_mean_r2')
                cv_corr = m.get('cv_mean_correlation')
                if cv_r2 is not None:
                    print(f"\n  CV metrics (cross-channel):")
                    print(f"    Mean R2:          {cv_r2:.4f}")
                    print(f"    Mean Correlation: {cv_corr:.4f}")
                print(f"\n  Saved to: {result['model_dir']}/")
            else:
                print(f"Training failed: {result.get('error', 'Unknown error')}")

        elif args.command == "fine-tune-embeddings":
            if result.get("success"):
                m = result["metrics"]
                print(f"Fine-tuning complete!")
                print(f"  Best epoch: {m['best_epoch']}/{m['total_epochs']}")
                print(f"  Best val loss: {m['best_val_loss']:.4f}")
                print(f"  Videos: {m['num_videos']}, Channels: {m['num_channels']}")
                print(f"  Projection dim: {m['projection_dim']}")
                print(f"  Vector store: {m['vector_store_size']} entries")
                print(f"  Device: {m['device']}")
            else:
                print(f"Fine-tuning failed: {result.get('error', 'Unknown error')}")

        elif args.command == "train-predictor":
            if result.get("success"):
                m = result.get("metrics", {})
                cv = m.get("cv_evaluation", {})
                print(f"Neural predictor trained successfully!")
                print(f"  Samples: {m.get('training_samples', '?')}, "
                      f"Channels: {m.get('unique_channels', '?')}")
                print(f"  Folds: {m.get('n_folds', '?')}, Epochs: {m.get('epochs', '?')}")
                print(f"  Device: {m.get('device', '?')}")
                if cv:
                    print(f"\n  CV metrics:")
                    print(f"    Mean R2:          {cv.get('mean_r2', 0):.4f}")
                    print(f"    Mean Correlation: {cv.get('mean_correlation', 0):.4f}")
                    print(f"    Mean RMSE:        {cv.get('mean_rmse', 0):.4f}")
            else:
                print(f"Training failed: {result.get('error', 'Unknown error')}")

        elif args.command == "predict":
            if result.get("error"):
                print(f"Error: {result['error']}")
            else:
                print(f"Video: {result.get('title', '')[:60]}")
                print(f"Channel: {result.get('channel', '')}")
                print(f"YT Views: {result.get('yt_views', 0):,}")
                preds = result.get("predictions", {})
                if preds.get("llm"):
                    llm = preds["llm"]
                    print(f"\nLLM Prediction:")
                    print(f"  Views: {llm['views']:,} (log={llm['log_views']})")
                    print(f"  Label: {llm['label']}")
                    print(f"  Confidence: {llm['confidence']}")
                    print(f"  Reasoning: {llm['reasoning']}")
                if preds.get("neural_predictor"):
                    print(f"\nNeural predictor: available at {preds['neural_predictor'].get('path')}")

        elif args.command == "discover":
            print(f"Recommendations: {result['total_recommendations']}")
            for i, rec in enumerate(result.get("recommendations", [])[:20], 1):
                label = rec.get("predicted_label") or "n/a"
                pv = rec.get("predicted_views")
                pv_str = f"{pv:,.0f}" if pv else "n/a"
                print(f"\n  #{i} [{rec['combined_score']:.4f}] {rec['youtube_title'][:60]}")
                print(f"     Strategy: {rec['strategy']} | Query: {rec['query_used'][:40]}")
                print(f"     Channel: {rec['youtube_channel']}")
                print(f"     YT views: {rec['youtube_views']:,} | Novelty: {rec['novelty_score']:.2f}")
                print(f"     Predicted: {pv_str} views ({label}) | Confidence: {rec['confidence']:.2f}")

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
                    print(f"    [{rec['combined_score']:.4f}] {rec['youtube_title'][:50]}")

        elif args.command == "fine-tune":
            if result.get("success"):
                print(f"Fine-tuning pipeline complete!")
                stats = result.get("data_stats", {})
                print(f"  Training examples: {stats.get('train_examples', '?')}")
                print(f"  Validation examples: {stats.get('val_examples', '?')}")
                if result.get("training"):
                    t = result["training"]
                    print(f"  Train loss: {t.get('train_loss', '?'):.4f}")
                    print(f"  Eval loss: {t.get('eval_loss', '?'):.4f}")
                if result.get("export"):
                    e = result["export"]
                    print(f"  Ollama model: {e.get('ollama_model', 'n/a')}")
            else:
                print(f"Fine-tuning failed: {result.get('error', 'Unknown error')}")

        elif args.command == "bootstrap":
            print(f"Strategies seeded: {result.get('strategies_seeded', 0)}")
            print(f"Channels seeded: {result.get('channels_seeded', 0)}")
            print(f"Scoring bootstrapped: {result.get('scoring_bootstrapped', False)}")
            print(f"LLM principles: {result.get('llm_principles', False)}")

        elif args.command == "strategy-list":
            print(f"Active strategies: {result['count']}")
            for s in result.get("strategies", []):
                yr = s.get("yield_rate", 0) or 0
                print(f"  {s['name']:<30} yield: {yr:.0%}  queries: {s.get('total_queries', 0)}")

        elif args.command == "strategy-add":
            if result.get("success"):
                print(f"Added strategy: {result['name']} (id={result['strategy_id']})")

        elif args.command == "follow-channel":
            if result.get("success"):
                print(f"Following channel: {result['channel_name']}")

        elif args.command == "skill-show":
            if result.get("error"):
                print(f"Error: {result['error']}")
            else:
                print(f"Skill: {result['name']} (v{result['version']})")
                print(f"Updated: {result.get('updated_at', 'n/a')}")
                print(f"\nSystem prompt:\n{result['system_prompt'][:500]}")
                if len(result['system_prompt']) > 500:
                    print(f"  ... ({len(result['system_prompt'])} chars total)")
                print(f"\nPrompt template:\n{result['prompt_template'][:500]}")
                if len(result['prompt_template']) > 500:
                    print(f"  ... ({len(result['prompt_template'])} chars total)")

        elif args.command == "skill-history":
            versions = result.get("versions", [])
            if not versions:
                print(f"No version history for '{result['skill_name']}'")
            else:
                print(f"Version history for '{result['skill_name']}':")
                for v in versions:
                    print(f"  v{v['version']} by {v['changed_by']} at {v['created_at']}")
                    if v.get('change_reason'):
                        print(f"    Reason: {v['change_reason'][:80]}")

        elif args.command == "skill-rollback":
            if result.get("success"):
                print(f"Rolled back '{result['skill_name']}' to version {result['target_version']}")
            else:
                print(f"Rollback failed: version {result['target_version']} not found")

        print(f"{'=' * 50}\n")


if __name__ == "__main__":
    asyncio.run(main())
