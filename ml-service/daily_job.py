#!/usr/bin/env python3
"""
Daily orchestrator: discover trending videos, pick the best, download & upload.

Designed to run via cron on a Linux VM.  All paths and tunables are read from
environment variables so the same script works locally and in production.

Env vars (all optional, sensible defaults provided):
    DB_PATH             Path to data.db          (default: data.db)
    TRANSPORT_BINARY    Path to yt-transfer       (default: ../yt-transfer)
    BILIUP_COOKIE       Path to cookies.json      (default: ../cookies.json)
    UPLOAD_COUNT        Videos to upload per run   (default: 2)
    MODEL_DIR           Trained model directory    (default: models)
    LLM_MODEL           Ollama model name          (default: qwen2.5:7b)
    LOG_DIR             Directory for daily logs   (default: ../logs)
    MAX_KEYWORDS        Trending keywords to fetch (default: 10)
    VIDEOS_PER_KEYWORD  YouTube results per kw     (default: 5)
    MAX_AGE_DAYS        Max video age in days       (default: 30)

Usage:
    python daily_job.py              # full run
    python daily_job.py --dry-run    # discover + pick, skip download/upload
"""
import argparse
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent

DB_PATH = os.environ.get("DB_PATH", str(SCRIPT_DIR / "data.db"))
_default_binary = SCRIPT_DIR.parent / ("yt-transfer.exe" if sys.platform == "win32" else "yt-transfer")
TRANSPORT_BINARY = os.environ.get("TRANSPORT_BINARY", str(_default_binary))
BILIUP_COOKIE = os.environ.get("BILIUP_COOKIE", str(SCRIPT_DIR.parent / "scripts" / "cookies.json"))
UPLOAD_COUNT = int(os.environ.get("UPLOAD_COUNT", "2"))
MODEL_DIR = os.environ.get("MODEL_DIR", str(SCRIPT_DIR / "models"))
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen2.5:7b")
LOG_DIR = os.environ.get("LOG_DIR", str(SCRIPT_DIR.parent / "logs"))
MAX_KEYWORDS = int(os.environ.get("MAX_KEYWORDS", "10"))
VIDEOS_PER_KEYWORD = int(os.environ.get("VIDEOS_PER_KEYWORD", "5"))
MAX_AGE_DAYS = int(os.environ.get("MAX_AGE_DAYS", "30"))

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging() -> logging.Logger:
    log_dir = Path(LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / f"daily_{datetime.now():%Y%m%d}.log"

    logger = logging.getLogger("daily_job")
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s")

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(
        open(sys.stdout.fileno(), mode="w", encoding="utf-8", errors="replace", closefd=False)
    )
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    return logger

# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def run_discovery(logger: logging.Logger) -> bool:
    """Run the discovery pipeline via CLI subprocess."""
    logger.info("=== Step 1: Running discovery pipeline ===")
    cmd = [
        sys.executable, "-m", "app.cli",
        "--db-path", DB_PATH, "--json",
        "discover",
        "--model-dir", MODEL_DIR,
        "--llm-model", LLM_MODEL,
        "--max-keywords", str(MAX_KEYWORDS),
        "--videos-per-keyword", str(VIDEOS_PER_KEYWORD),
        "--max-age-days", str(MAX_AGE_DAYS),
    ]
    logger.info("CMD: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        cwd=str(SCRIPT_DIR),
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        logger.error("Discovery failed (exit %d):\n%s", result.returncode, result.stderr)
        return False
    logger.info("Discovery stdout:\n%s", result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout)
    return True


def pick_videos(logger: logging.Logger):
    """Query DB for top pending recommendations."""
    logger.info("=== Step 2: Picking top %d pending videos ===", UPLOAD_COUNT)

    # Import here so the rest of the script can be parsed without deps
    sys.path.insert(0, str(SCRIPT_DIR))
    from app.db.database import Database

    with Database(DB_PATH) as db:
        db.ensure_discovery_tables()
        picks = db.get_pending_recommendations(limit=UPLOAD_COUNT)

    for i, p in enumerate(picks, 1):
        logger.info(
            "  #%d  %s  score=%.4f  views=%s  label=%s  -- %s",
            i, p["youtube_video_id"], p["combined_score"],
            p["predicted_views"], p["predicted_label"], p["youtube_title"],
        )
    return picks


def transfer_video(video_id: str, logger: logging.Logger) -> bool:
    """Shell out to yt-transfer to download + upload one video."""
    cmd = [
        TRANSPORT_BINARY,
        "--video-id", video_id,
        "--platform", "bilibili",
        "--biliup-cookie", BILIUP_COOKIE,
    ]
    logger.info("CMD: %s", " ".join(cmd))
    # Add venv Scripts dir to PATH so yt-transfer can find yt-dlp & biliup
    env = os.environ.copy()
    venv_bin = str(SCRIPT_DIR / ".venv" / ("Scripts" if sys.platform == "win32" else "bin"))
    env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=1800,  # 30 min per video
    )
    result.stdout = result.stdout.decode("utf-8", errors="replace")
    result.stderr = result.stderr.decode("utf-8", errors="replace")
    if result.returncode != 0:
        logger.error("yt-transfer failed (exit %d):\n%s", result.returncode, result.stderr)
        return False
    logger.info("yt-transfer stdout:\n%s", result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout)
    return True


def mark_uploaded(video_id: str, logger: logging.Logger) -> None:
    """Mark a recommendation as uploaded in the DB."""
    sys.path.insert(0, str(SCRIPT_DIR))
    from app.db.database import Database

    with Database(DB_PATH) as db:
        db.ensure_discovery_tables()
        db.mark_recommendation_uploaded(video_id)
    logger.info("Marked %s as uploaded", video_id)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Daily discover + upload job")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run discovery and pick videos but skip download/upload",
    )
    args = parser.parse_args()

    logger = setup_logging()
    logger.info("======== Daily job started (dry_run=%s) ========", args.dry_run)

    # Step 1: discovery
    if not run_discovery(logger):
        logger.error("Discovery failed, aborting")
        sys.exit(1)

    # Step 2: pick videos
    picks = pick_videos(logger)
    if not picks:
        logger.info("No pending recommendations found. Nothing to do.")
        sys.exit(0)

    if args.dry_run:
        logger.info("--dry-run: skipping download/upload")
        logger.info("======== Daily job finished (dry run) ========")
        return

    # Step 3 & 4: transfer + mark
    logger.info("=== Step 3: Downloading & uploading %d videos ===", len(picks))
    uploaded = 0
    failed = 0
    for pick in picks:
        vid = pick["youtube_video_id"]
        title = pick["youtube_title"]
        logger.info("--- Processing: %s (%s) ---", vid, title)
        try:
            ok = transfer_video(vid, logger)
            if ok:
                mark_uploaded(vid, logger)
                uploaded += 1
            else:
                failed += 1
        except Exception:
            logger.exception("Unexpected error processing %s", vid)
            failed += 1

    logger.info("======== Daily job finished: %d uploaded, %d failed ========", uploaded, failed)


if __name__ == "__main__":
    main()
