"""
Batch collection script: add all confirmed transporter channels and collect videos.
Run this once to populate the database with training data.
"""
import asyncio
import logging
import sys
import time

sys.path.insert(0, ".")
from app.db.database import Database
from app.collectors.competitor_monitor import CompetitorMonitor
from app.collectors.labeler import Labeler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("collection_log.txt", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# Confirmed independent YouTube transporter channels
CHANNELS = [
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

DB_PATH = "data.db"
VIDEOS_PER_CHANNEL = 300  # Collect up to 300 per channel


async def main():
    start_time = time.time()

    with Database(DB_PATH) as db:
        db.ensure_competitor_tables()
        monitor = CompetitorMonitor(db, rate_limit=1.0)

        # Phase 1: Add all channels
        logger.info("=" * 60)
        logger.info("PHASE 1: Adding competitor channels")
        logger.info("=" * 60)

        for uid, name, category in CHANNELS:
            logger.info(f"Adding channel: {name} ({uid}) [{category}]")
            try:
                channel = await monitor.get_channel_info(uid)
                if channel:
                    db.add_competitor_channel(channel)
                    logger.info(f"  -> Added: {channel.name} ({channel.follower_count} followers)")
                else:
                    logger.warning(f"  -> Could not find channel {uid}")
            except Exception as e:
                logger.error(f"  -> Error adding {uid}: {e}")

        # Phase 2: Collect videos from all channels
        logger.info("")
        logger.info("=" * 60)
        logger.info(f"PHASE 2: Collecting up to {VIDEOS_PER_CHANNEL} videos per channel")
        logger.info("=" * 60)

        total_collected = 0
        total_with_yt = 0

        for uid, name, category in CHANNELS:
            logger.info(f"\nCollecting from: {name} ({uid})")
            try:
                collected, with_yt = await monitor.collect_channel(uid, VIDEOS_PER_CHANNEL)
                total_collected += collected
                total_with_yt += with_yt
                logger.info(f"  -> Collected {collected} videos ({with_yt} with YouTube source)")
            except Exception as e:
                logger.error(f"  -> Error collecting from {uid}: {e}")

        # Phase 3: Label all collected videos
        logger.info("")
        logger.info("=" * 60)
        logger.info("PHASE 3: Labeling videos")
        logger.info("=" * 60)

        labeler = Labeler(db)
        results = labeler.label_all_unlabeled(limit=10000)
        logger.info(f"Labeled {results['total']} videos:")
        logger.info(f"  viral:      {results.get('viral', 0)}")
        logger.info(f"  successful: {results.get('successful', 0)}")
        logger.info(f"  standard:   {results.get('standard', 0)}")
        logger.info(f"  failed:     {results.get('failed', 0)}")

        # Phase 4: Summary
        elapsed = time.time() - start_time
        logger.info("")
        logger.info("=" * 60)
        logger.info("COLLECTION COMPLETE")
        logger.info("=" * 60)
        logger.info(f"Total videos collected: {total_collected}")
        logger.info(f"With YouTube source:    {total_with_yt}")
        logger.info(f"Time elapsed:           {elapsed/60:.1f} minutes")
        logger.info(f"Database:               {DB_PATH}")

        # Show training readiness
        summary = db.get_training_data_summary()
        logger.info(f"\nTraining data summary:")
        logger.info(f"  Total labeled:  {summary.get('total', 0)}")
        logger.info(f"  viral:          {summary.get('viral', 0)}")
        logger.info(f"  successful:     {summary.get('successful', 0)}")
        logger.info(f"  standard:       {summary.get('standard', 0)}")
        logger.info(f"  failed:         {summary.get('failed', 0)}")

        total_labeled = summary.get('total', 0)
        if total_labeled >= 50:
            logger.info("\n  READY TO TRAIN! Run:")
            logger.info(f"  python -m app.cli --db-path {DB_PATH} train --gpu")
        else:
            logger.info(f"\n  Need at least 50 labeled samples (have {total_labeled})")


if __name__ == "__main__":
    asyncio.run(main())
