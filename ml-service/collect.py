"""
Batch collection script: add transporter channels and collect videos.
Supports multiple rounds of collection via --round argument.
"""
import argparse
import asyncio
import logging
import sys
import time

sys.path.insert(0, ".")
from app.db.database import Database
from app.collectors.competitor_monitor import CompetitorMonitor
from app.collectors.labeler import Labeler

# Round 1: Confirmed independent YouTube transporter channels
ROUND_1_CHANNELS = [
    ("375797865", "试炼与梦想", "entertainment/dating"),
    ("4390568", "瑶锅不是锅", "gaming (PewDiePie etc.)"),
    ("544112908", "难绷funny", "comedy/viral clips"),
    ("677245745", "油管心理咨询搬运", "psychology/counseling"),
    ("662792114", "苜蓿在你的", "mixed YouTube transport"),
    ("325069537", "Stefanie英语日记", "English learning vlogs"),
    ("3546858064448344", "TED听力精选课", "TED/English learning"),
    ("17198256", "佐倉熊", "Japanese idol content"),
    ("1121309703", "YouTube外语大世界", "English learning"),
    ("50944796", "早学笔记", "English vlog transport"),
]

# Round 2: Channels with high YouTube ID reference rates
ROUND_2_CHANNELS = [
    ("121473911", "wdygggh", "32523 videos, 100% YT rate"),
    ("40291327", "unknown-large", "30088 videos, 67% YT rate"),
    ("1263732318", "unknown-1", "14656 videos, 100% YT rate"),
    ("153950464", "unknown-2", "18474 videos, 50% YT rate"),
    ("3546839982803255", "unknown-3", "6457 videos, 100% YT rate"),
    ("6756999", "unknown-4", "3299 videos, 100% YT rate"),
    ("66795887", "unknown-5", "2002 videos, 100% YT rate"),
    ("65410812", "unknown-6", "2005 videos, 93% YT rate"),
    ("401400666", "vector090_", "1487 videos, 100% YT rate"),
    ("3773620", "unknown-7", "799 videos, 93% YT rate"),
    ("6856883", "unknown-8", "739 videos, 100% YT rate"),
    ("3457624", "-LilyPichu-", "939 videos, 73% YT rate"),
    ("21416270", "unknown-9", "560 videos, 97% YT rate"),
    ("475981121", "unknown-10", "508 videos, 100% YT rate"),
    ("33708661", "unknown-11", "502 videos, 100% YT rate"),
    ("23534705", "unknown-12", "464 videos, 90% YT rate"),
    ("13170390", "unknown-13", "269 videos, 100% YT rate"),
    ("520908683", "unknown-14", "231 videos, 100% YT rate"),
    ("816198", "Barnett-Wong", "223 videos, 97% YT rate"),
    ("3461582812088629", "Kurzgesagt-CN", "103 videos, 93% YT rate"),
    ("10523655", "unknown-15", "103 videos, 87% YT rate"),
]

ROUNDS = {
    1: ("Round 1", ROUND_1_CHANNELS, "collection_log.txt"),
    2: ("Round 2", ROUND_2_CHANNELS, "collection_round2_log.txt"),
}

DB_PATH = "data.db"
VIDEOS_PER_CHANNEL = 300


async def run_collection(round_num: int):
    round_name, channels, log_file = ROUNDS[round_num]
    start_time = time.time()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
    logger = logging.getLogger(__name__)

    with Database(DB_PATH) as db:
        db.ensure_competitor_tables()
        monitor = CompetitorMonitor(db, rate_limit=1.0)

        # Phase 1: Add channels
        logger.info("=" * 60)
        logger.info(f"{round_name}: Adding {len(channels)} channels")
        logger.info("=" * 60)

        for uid, name, info in channels:
            logger.info(f"Adding: {name} ({uid}) [{info}]")
            try:
                channel = await monitor.get_channel_info(uid)
                if channel:
                    db.add_competitor_channel(channel)
                    logger.info(f"  -> Added: {channel.name} ({channel.follower_count:,} followers)")
                else:
                    logger.warning(f"  -> Could not find channel {uid}")
            except Exception as e:
                logger.error(f"  -> Error: {e}")

        # Phase 2: Collect videos
        logger.info("")
        logger.info("=" * 60)
        logger.info(f"Collecting up to {VIDEOS_PER_CHANNEL} videos per channel")
        logger.info("=" * 60)

        total_collected = 0
        total_with_yt = 0

        for uid, name, info in channels:
            logger.info(f"\nCollecting from: {name} ({uid})")
            try:
                collected, with_yt = await monitor.collect_channel(uid, VIDEOS_PER_CHANNEL)
                total_collected += collected
                total_with_yt += with_yt
                logger.info(f"  -> {collected} videos ({with_yt} with YouTube source)")
            except Exception as e:
                logger.error(f"  -> Error: {e}")

        # Phase 3: Label
        logger.info("")
        logger.info("=" * 60)
        logger.info("Labeling videos")
        logger.info("=" * 60)

        labeler = Labeler(db)
        results = labeler.label_all_unlabeled(limit=20000)
        logger.info(f"Labeled {results['total']} videos:")
        for label in ["viral", "successful", "standard", "failed"]:
            logger.info(f"  {label}: {results.get(label, 0)}")

        # Summary
        elapsed = time.time() - start_time
        logger.info("")
        logger.info("=" * 60)
        logger.info(f"{round_name} COMPLETE")
        logger.info("=" * 60)
        logger.info(f"Videos collected: {total_collected}")
        logger.info(f"With YouTube source: {total_with_yt}")
        logger.info(f"Time: {elapsed/60:.1f} minutes")
        logger.info(f"Database: {DB_PATH}")

        summary = db.get_training_data_summary()
        logger.info(f"\nTraining data summary:")
        logger.info(f"  Total: {summary.get('total', 0)}")
        for label in ["viral", "successful", "standard", "failed", "unlabeled"]:
            logger.info(f"  {label}: {summary.get(label, 0)}")

        total_labeled = summary.get('total', 0)
        if total_labeled >= 50:
            logger.info(f"\n  READY TO TRAIN! Run:")
            logger.info(f"  python -m app.cli --db-path {DB_PATH} train --gpu")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect videos from Bilibili transporter channels")
    parser.add_argument("--round", type=int, choices=list(ROUNDS.keys()), default=1,
                        help="Collection round (1 or 2)")
    args = parser.parse_args()
    asyncio.run(run_collection(args.round))
