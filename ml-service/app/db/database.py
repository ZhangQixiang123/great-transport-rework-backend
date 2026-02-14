"""
Database adapter supporting both SQLite and PostgreSQL.
"""
import os
import sqlite3
from datetime import datetime
from typing import Optional, List, Dict, Any
from dataclasses import dataclass


@dataclass
class Upload:
    """Represents an uploaded video."""
    video_id: str
    channel_id: str
    bilibili_bvid: str
    uploaded_at: datetime


@dataclass
class UploadPerformance:
    """Performance metrics for an upload at a checkpoint."""
    id: Optional[int]
    upload_id: str
    checkpoint_hours: int
    recorded_at: datetime
    views: int
    likes: int
    coins: int
    favorites: int
    shares: int
    danmaku: int
    comments: int
    view_velocity: float
    engagement_rate: float


@dataclass
class UploadOutcome:
    """Final outcome label for an upload."""
    id: Optional[int]
    upload_id: str
    label: str  # viral, successful, standard, failed
    labeled_at: datetime
    final_views: int
    final_engagement_rate: float
    final_coins: int


@dataclass
class CompetitorChannel:
    """Represents a Bilibili transporter channel to monitor."""
    bilibili_uid: str
    name: str
    description: str
    follower_count: int
    video_count: int
    added_at: datetime
    is_active: bool


@dataclass
class CompetitorVideo:
    """Represents a video from a competitor channel."""
    bvid: str
    bilibili_uid: str
    title: str
    description: str
    duration: int
    views: int
    likes: int
    coins: int
    favorites: int
    shares: int
    danmaku: int
    comments: int
    publish_time: Optional[datetime]
    collected_at: datetime
    youtube_source_id: Optional[str]
    label: Optional[str]


class Database:
    """Database adapter for performance tracking."""

    def __init__(self, connection_string: str):
        """
        Initialize database connection.

        Args:
            connection_string: SQLite path or PostgreSQL connection string.
                               For SQLite: path to .db file
                               For PostgreSQL: postgresql://user:pass@host:port/db
        """
        self.connection_string = connection_string
        self._is_postgres = connection_string.startswith("postgresql://")
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        """Establish database connection."""
        if self._is_postgres:
            raise NotImplementedError("PostgreSQL support coming in production phase")
        else:
            self._conn = sqlite3.connect(self.connection_string)
            self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def get_uploads_for_tracking(self, checkpoint_hours: int) -> List[Upload]:
        """
        Get uploads that need performance tracking at the specified checkpoint.

        Args:
            checkpoint_hours: The checkpoint to check for (1, 6, 24, 48, 168, 720)

        Returns:
            List of uploads due for tracking at this checkpoint.
        """
        if not self._conn:
            raise RuntimeError("Database not connected")

        cursor = self._conn.execute("""
            SELECT u.video_id, u.channel_id, u.bilibili_bvid, u.uploaded_at
            FROM uploads u
            WHERE u.bilibili_bvid IS NOT NULL AND u.bilibili_bvid != ''
              AND datetime(u.uploaded_at, '+' || ? || ' hours') <= datetime('now')
              AND NOT EXISTS (
                SELECT 1 FROM upload_performance up
                WHERE up.upload_id = u.video_id AND up.checkpoint_hours = ?
              )
            ORDER BY u.uploaded_at
        """, (checkpoint_hours, checkpoint_hours))

        uploads = []
        for row in cursor.fetchall():
            uploads.append(Upload(
                video_id=row["video_id"],
                channel_id=row["channel_id"],
                bilibili_bvid=row["bilibili_bvid"],
                uploaded_at=datetime.fromisoformat(row["uploaded_at"].replace("Z", "+00:00") if isinstance(row["uploaded_at"], str) else str(row["uploaded_at"]))
            ))
        return uploads

    def get_all_uploads_with_bvid(self) -> List[Upload]:
        """Get all uploads that have a Bilibili bvid."""
        if not self._conn:
            raise RuntimeError("Database not connected")

        cursor = self._conn.execute("""
            SELECT video_id, channel_id, bilibili_bvid, uploaded_at
            FROM uploads
            WHERE bilibili_bvid IS NOT NULL AND bilibili_bvid != ''
            ORDER BY uploaded_at DESC
        """)

        uploads = []
        for row in cursor.fetchall():
            uploaded_at = row["uploaded_at"]
            if isinstance(uploaded_at, str):
                uploaded_at = datetime.fromisoformat(uploaded_at.replace("Z", "+00:00"))
            uploads.append(Upload(
                video_id=row["video_id"],
                channel_id=row["channel_id"],
                bilibili_bvid=row["bilibili_bvid"],
                uploaded_at=uploaded_at
            ))
        return uploads

    def save_performance(self, perf: UploadPerformance) -> None:
        """
        Save performance metrics for an upload.

        Args:
            perf: Performance metrics to save.
        """
        if not self._conn:
            raise RuntimeError("Database not connected")

        self._conn.execute("""
            INSERT INTO upload_performance
                (upload_id, checkpoint_hours, recorded_at, views, likes, coins,
                 favorites, shares, danmaku, comments, view_velocity, engagement_rate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(upload_id, checkpoint_hours) DO UPDATE SET
                recorded_at = excluded.recorded_at,
                views = excluded.views,
                likes = excluded.likes,
                coins = excluded.coins,
                favorites = excluded.favorites,
                shares = excluded.shares,
                danmaku = excluded.danmaku,
                comments = excluded.comments,
                view_velocity = excluded.view_velocity,
                engagement_rate = excluded.engagement_rate
        """, (
            perf.upload_id, perf.checkpoint_hours, perf.recorded_at.isoformat(),
            perf.views, perf.likes, perf.coins, perf.favorites, perf.shares,
            perf.danmaku, perf.comments, perf.view_velocity, perf.engagement_rate
        ))
        self._conn.commit()

    def save_outcome(self, outcome: UploadOutcome) -> None:
        """
        Save outcome label for an upload.

        Args:
            outcome: Outcome to save.
        """
        if not self._conn:
            raise RuntimeError("Database not connected")

        self._conn.execute("""
            INSERT INTO upload_outcomes
                (upload_id, label, labeled_at, final_views, final_engagement_rate, final_coins)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(upload_id) DO UPDATE SET
                label = excluded.label,
                labeled_at = excluded.labeled_at,
                final_views = excluded.final_views,
                final_engagement_rate = excluded.final_engagement_rate,
                final_coins = excluded.final_coins
        """, (
            outcome.upload_id, outcome.label, outcome.labeled_at.isoformat(),
            outcome.final_views, outcome.final_engagement_rate, outcome.final_coins
        ))
        self._conn.commit()

    def get_latest_performance(self, upload_id: str) -> Optional[UploadPerformance]:
        """Get the latest performance record for an upload."""
        if not self._conn:
            raise RuntimeError("Database not connected")

        cursor = self._conn.execute("""
            SELECT id, upload_id, checkpoint_hours, recorded_at, views, likes, coins,
                   favorites, shares, danmaku, comments, view_velocity, engagement_rate
            FROM upload_performance
            WHERE upload_id = ?
            ORDER BY checkpoint_hours DESC
            LIMIT 1
        """, (upload_id,))

        row = cursor.fetchone()
        if not row:
            return None

        recorded_at = row["recorded_at"]
        if isinstance(recorded_at, str):
            recorded_at = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))

        return UploadPerformance(
            id=row["id"],
            upload_id=row["upload_id"],
            checkpoint_hours=row["checkpoint_hours"],
            recorded_at=recorded_at,
            views=row["views"],
            likes=row["likes"],
            coins=row["coins"],
            favorites=row["favorites"],
            shares=row["shares"],
            danmaku=row["danmaku"],
            comments=row["comments"],
            view_velocity=row["view_velocity"],
            engagement_rate=row["engagement_rate"]
        )

    def get_uploads_for_labeling(self, min_checkpoint_hours: int = 720) -> List[Upload]:
        """
        Get uploads that have enough data for labeling (default: 30 days).

        Args:
            min_checkpoint_hours: Minimum checkpoint hours required (default 720 = 30 days)

        Returns:
            List of uploads ready for labeling.
        """
        if not self._conn:
            raise RuntimeError("Database not connected")

        cursor = self._conn.execute("""
            SELECT u.video_id, u.channel_id, u.bilibili_bvid, u.uploaded_at
            FROM uploads u
            WHERE u.bilibili_bvid IS NOT NULL AND u.bilibili_bvid != ''
              AND EXISTS (
                SELECT 1 FROM upload_performance up
                WHERE up.upload_id = u.video_id AND up.checkpoint_hours >= ?
              )
              AND NOT EXISTS (
                SELECT 1 FROM upload_outcomes uo
                WHERE uo.upload_id = u.video_id
              )
            ORDER BY u.uploaded_at
        """, (min_checkpoint_hours,))

        uploads = []
        for row in cursor.fetchall():
            uploaded_at = row["uploaded_at"]
            if isinstance(uploaded_at, str):
                uploaded_at = datetime.fromisoformat(uploaded_at.replace("Z", "+00:00"))
            uploads.append(Upload(
                video_id=row["video_id"],
                channel_id=row["channel_id"],
                bilibili_bvid=row["bilibili_bvid"],
                uploaded_at=uploaded_at
            ))
        return uploads

    # Phase 3B: Competitor Monitoring Methods

    def ensure_competitor_tables(self) -> None:
        """Create competitor tables if they don't exist."""
        if not self._conn:
            raise RuntimeError("Database not connected")

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS competitor_channels (
                bilibili_uid TEXT PRIMARY KEY,
                name TEXT,
                description TEXT,
                follower_count INTEGER DEFAULT 0,
                video_count INTEGER DEFAULT 0,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER DEFAULT 1
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS competitor_videos (
                bvid TEXT PRIMARY KEY,
                bilibili_uid TEXT NOT NULL,
                title TEXT,
                description TEXT,
                duration INTEGER DEFAULT 0,
                views INTEGER DEFAULT 0,
                likes INTEGER DEFAULT 0,
                coins INTEGER DEFAULT 0,
                favorites INTEGER DEFAULT 0,
                shares INTEGER DEFAULT 0,
                danmaku INTEGER DEFAULT 0,
                comments INTEGER DEFAULT 0,
                publish_time TIMESTAMP,
                collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                youtube_source_id TEXT,
                label TEXT,
                FOREIGN KEY (bilibili_uid) REFERENCES competitor_channels(bilibili_uid)
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_competitor_videos_uid
            ON competitor_videos(bilibili_uid)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_competitor_videos_label
            ON competitor_videos(label)
        """)
        self._conn.commit()

    def add_competitor_channel(self, channel: CompetitorChannel) -> None:
        """Add or update a competitor channel."""
        if not self._conn:
            raise RuntimeError("Database not connected")

        self._conn.execute("""
            INSERT INTO competitor_channels
                (bilibili_uid, name, description, follower_count, video_count, added_at, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(bilibili_uid) DO UPDATE SET
                name = COALESCE(excluded.name, competitor_channels.name),
                description = COALESCE(excluded.description, competitor_channels.description),
                follower_count = excluded.follower_count,
                video_count = excluded.video_count,
                is_active = 1
        """, (
            channel.bilibili_uid, channel.name, channel.description,
            channel.follower_count, channel.video_count,
            channel.added_at.isoformat(), 1 if channel.is_active else 0
        ))
        self._conn.commit()

    def list_competitor_channels(self, active_only: bool = True) -> List[CompetitorChannel]:
        """List competitor channels."""
        if not self._conn:
            raise RuntimeError("Database not connected")

        query = """
            SELECT bilibili_uid, name, description, follower_count, video_count, added_at, is_active
            FROM competitor_channels
        """
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY added_at"

        cursor = self._conn.execute(query)
        channels = []
        for row in cursor.fetchall():
            added_at = row["added_at"]
            if isinstance(added_at, str):
                added_at = datetime.fromisoformat(added_at.replace("Z", "+00:00"))
            channels.append(CompetitorChannel(
                bilibili_uid=row["bilibili_uid"],
                name=row["name"] or "",
                description=row["description"] or "",
                follower_count=row["follower_count"] or 0,
                video_count=row["video_count"] or 0,
                added_at=added_at,
                is_active=row["is_active"] == 1
            ))
        return channels

    def deactivate_competitor_channel(self, uid: str) -> None:
        """Deactivate a competitor channel."""
        if not self._conn:
            raise RuntimeError("Database not connected")

        self._conn.execute(
            "UPDATE competitor_channels SET is_active = 0 WHERE bilibili_uid = ?",
            (uid,)
        )
        self._conn.commit()

    def save_competitor_video(self, video: CompetitorVideo) -> None:
        """Save or update a competitor video."""
        if not self._conn:
            raise RuntimeError("Database not connected")

        publish_time = video.publish_time.isoformat() if video.publish_time else None

        self._conn.execute("""
            INSERT INTO competitor_videos
                (bvid, bilibili_uid, title, description, duration, views, likes, coins,
                 favorites, shares, danmaku, comments, publish_time, collected_at,
                 youtube_source_id, label)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(bvid) DO UPDATE SET
                views = excluded.views,
                likes = excluded.likes,
                coins = excluded.coins,
                favorites = excluded.favorites,
                shares = excluded.shares,
                danmaku = excluded.danmaku,
                comments = excluded.comments,
                collected_at = excluded.collected_at,
                youtube_source_id = COALESCE(excluded.youtube_source_id, competitor_videos.youtube_source_id),
                label = COALESCE(excluded.label, competitor_videos.label)
        """, (
            video.bvid, video.bilibili_uid, video.title, video.description,
            video.duration, video.views, video.likes, video.coins,
            video.favorites, video.shares, video.danmaku, video.comments,
            publish_time, video.collected_at.isoformat(),
            video.youtube_source_id, video.label
        ))
        self._conn.commit()

    def get_competitor_videos(
        self,
        uid: Optional[str] = None,
        label: Optional[str] = None,
        limit: int = 100
    ) -> List[CompetitorVideo]:
        """Get competitor videos with optional filters."""
        if not self._conn:
            raise RuntimeError("Database not connected")

        query = """
            SELECT bvid, bilibili_uid, title, description, duration, views, likes, coins,
                   favorites, shares, danmaku, comments, publish_time, collected_at,
                   youtube_source_id, label
            FROM competitor_videos
            WHERE 1=1
        """
        params: List[Any] = []

        if uid:
            query += " AND bilibili_uid = ?"
            params.append(uid)
        if label:
            if label == "unlabeled":
                query += " AND (label IS NULL OR label = '')"
            else:
                query += " AND label = ?"
                params.append(label)

        query += " ORDER BY views DESC LIMIT ?"
        params.append(limit)

        cursor = self._conn.execute(query, params)
        videos = []
        for row in cursor.fetchall():
            publish_time = row["publish_time"]
            if publish_time and isinstance(publish_time, str):
                publish_time = datetime.fromisoformat(publish_time.replace("Z", "+00:00"))

            collected_at = row["collected_at"]
            if isinstance(collected_at, str):
                collected_at = datetime.fromisoformat(collected_at.replace("Z", "+00:00"))

            videos.append(CompetitorVideo(
                bvid=row["bvid"],
                bilibili_uid=row["bilibili_uid"],
                title=row["title"] or "",
                description=row["description"] or "",
                duration=row["duration"] or 0,
                views=row["views"] or 0,
                likes=row["likes"] or 0,
                coins=row["coins"] or 0,
                favorites=row["favorites"] or 0,
                shares=row["shares"] or 0,
                danmaku=row["danmaku"] or 0,
                comments=row["comments"] or 0,
                publish_time=publish_time,
                collected_at=collected_at,
                youtube_source_id=row["youtube_source_id"],
                label=row["label"]
            ))
        return videos

    def get_labeled_competitor_videos(self) -> List[CompetitorVideo]:
        """Get all competitor videos that have a valid label (for training).

        Returns videos ordered by publish_time ASC for reproducible splits.
        """
        if not self._conn:
            raise RuntimeError("Database not connected")

        cursor = self._conn.execute("""
            SELECT bvid, bilibili_uid, title, description, duration, views, likes, coins,
                   favorites, shares, danmaku, comments, publish_time, collected_at,
                   youtube_source_id, label
            FROM competitor_videos
            WHERE label IS NOT NULL AND label != ''
            ORDER BY publish_time ASC
        """)

        videos = []
        for row in cursor.fetchall():
            publish_time = row["publish_time"]
            if publish_time and isinstance(publish_time, str):
                publish_time = datetime.fromisoformat(publish_time.replace("Z", "+00:00"))

            collected_at = row["collected_at"]
            if isinstance(collected_at, str):
                collected_at = datetime.fromisoformat(collected_at.replace("Z", "+00:00"))

            videos.append(CompetitorVideo(
                bvid=row["bvid"],
                bilibili_uid=row["bilibili_uid"],
                title=row["title"] or "",
                description=row["description"] or "",
                duration=row["duration"] or 0,
                views=row["views"] or 0,
                likes=row["likes"] or 0,
                coins=row["coins"] or 0,
                favorites=row["favorites"] or 0,
                shares=row["shares"] or 0,
                danmaku=row["danmaku"] or 0,
                comments=row["comments"] or 0,
                publish_time=publish_time,
                collected_at=collected_at,
                youtube_source_id=row["youtube_source_id"],
                label=row["label"]
            ))
        return videos

    def get_unlabeled_competitor_videos(self, limit: int = 1000) -> List[CompetitorVideo]:
        """Get competitor videos that haven't been labeled yet."""
        return self.get_competitor_videos(label="unlabeled", limit=limit)

    def update_competitor_video_label(self, bvid: str, label: str) -> None:
        """Update the label for a competitor video."""
        if not self._conn:
            raise RuntimeError("Database not connected")

        self._conn.execute(
            "UPDATE competitor_videos SET label = ? WHERE bvid = ?",
            (label, bvid)
        )
        self._conn.commit()

    def get_training_data_summary(self) -> Dict[str, int]:
        """Get counts of competitor videos by label."""
        if not self._conn:
            raise RuntimeError("Database not connected")

        cursor = self._conn.execute("""
            SELECT COALESCE(label, 'unlabeled') as lbl, COUNT(*) as cnt
            FROM competitor_videos
            GROUP BY COALESCE(label, 'unlabeled')
        """)

        summary: Dict[str, int] = {
            "viral": 0,
            "successful": 0,
            "standard": 0,
            "failed": 0,
            "unlabeled": 0,
            "total": 0
        }

        for row in cursor.fetchall():
            label = row["lbl"]
            count = row["cnt"]
            if label in summary:
                summary[label] = count
            else:
                summary["unlabeled"] += count
            summary["total"] += count

        return summary

    # Discovery Pipeline Methods

    def ensure_discovery_tables(self) -> None:
        """Create discovery pipeline tables if they don't exist."""
        if not self._conn:
            raise RuntimeError("Database not connected")

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS discovery_runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                keywords_fetched INTEGER,
                candidates_found INTEGER,
                recommendations_count INTEGER
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS discovery_recommendations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER REFERENCES discovery_runs(run_id),
                keyword TEXT NOT NULL,
                heat_score INTEGER,
                youtube_video_id TEXT NOT NULL,
                youtube_title TEXT,
                youtube_channel TEXT,
                youtube_views INTEGER,
                youtube_likes INTEGER,
                youtube_duration_seconds INTEGER,
                relevance_score REAL,
                relevance_reasoning TEXT,
                predicted_log_views REAL,
                predicted_views REAL,
                predicted_label TEXT,
                combined_score REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_discovery_rec_run
            ON discovery_recommendations(run_id)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_discovery_rec_score
            ON discovery_recommendations(combined_score DESC)
        """)
        self._conn.commit()

    def save_discovery_run(
        self, keywords_fetched: int, candidates_found: int,
        recommendations_count: int,
    ) -> int:
        """Save a discovery run record and return its run_id."""
        if not self._conn:
            raise RuntimeError("Database not connected")

        cursor = self._conn.execute("""
            INSERT INTO discovery_runs
                (run_at, keywords_fetched, candidates_found, recommendations_count)
            VALUES (?, ?, ?, ?)
        """, (
            datetime.now().isoformat(), keywords_fetched,
            candidates_found, recommendations_count,
        ))
        self._conn.commit()
        return cursor.lastrowid

    def save_recommendations(self, run_id: int, recommendations) -> None:
        """Save a batch of recommendations for a discovery run.

        Args:
            run_id: ID of the discovery run.
            recommendations: List of Recommendation dataclass instances.
        """
        if not self._conn:
            raise RuntimeError("Database not connected")

        for rec in recommendations:
            self._conn.execute("""
                INSERT INTO discovery_recommendations
                    (run_id, keyword, heat_score, youtube_video_id,
                     youtube_title, youtube_channel, youtube_views,
                     youtube_likes, youtube_duration_seconds,
                     relevance_score, relevance_reasoning,
                     predicted_log_views, predicted_views, predicted_label,
                     combined_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run_id, rec.keyword, rec.heat_score, rec.youtube_video_id,
                rec.youtube_title, rec.youtube_channel, rec.youtube_views,
                rec.youtube_likes, rec.youtube_duration_seconds,
                rec.relevance_score, rec.relevance_reasoning,
                rec.predicted_log_views, rec.predicted_views,
                rec.predicted_label, rec.combined_score,
            ))
        self._conn.commit()

    def get_discovery_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent discovery run summaries with top recommendations.

        Args:
            limit: Max number of runs to return.

        Returns:
            List of dicts with run info and top recommendations.
        """
        if not self._conn:
            raise RuntimeError("Database not connected")

        runs = self._conn.execute("""
            SELECT run_id, run_at, keywords_fetched,
                   candidates_found, recommendations_count
            FROM discovery_runs
            ORDER BY run_at DESC
            LIMIT ?
        """, (limit,)).fetchall()

        results = []
        for run in runs:
            recs = self._conn.execute("""
                SELECT keyword, heat_score, youtube_video_id, youtube_title,
                       youtube_channel, youtube_views, relevance_score,
                       predicted_views, predicted_label, combined_score
                FROM discovery_recommendations
                WHERE run_id = ?
                ORDER BY combined_score DESC
                LIMIT 10
            """, (run["run_id"],)).fetchall()

            results.append({
                "run_id": run["run_id"],
                "run_at": run["run_at"],
                "keywords_fetched": run["keywords_fetched"],
                "candidates_found": run["candidates_found"],
                "recommendations_count": run["recommendations_count"],
                "top_recommendations": [
                    {
                        "keyword": r["keyword"],
                        "heat_score": r["heat_score"],
                        "youtube_video_id": r["youtube_video_id"],
                        "youtube_title": r["youtube_title"],
                        "youtube_channel": r["youtube_channel"],
                        "youtube_views": r["youtube_views"],
                        "relevance_score": r["relevance_score"],
                        "predicted_views": r["predicted_views"],
                        "predicted_label": r["predicted_label"],
                        "combined_score": r["combined_score"],
                    }
                    for r in recs
                ],
            })

        return results

    def get_already_transported_yt_ids(self) -> set:
        """Return YouTube video IDs that have already been transported or recommended.

        Checks both:
        - competitor_videos.youtube_source_id (already on Bilibili)
        - discovery_recommendations.youtube_video_id (previously recommended)
        """
        if not self._conn:
            raise RuntimeError("Database not connected")

        ids = set()

        # Videos already transported to Bilibili
        try:
            rows = self._conn.execute("""
                SELECT DISTINCT youtube_source_id FROM competitor_videos
                WHERE youtube_source_id IS NOT NULL AND youtube_source_id != ''
            """).fetchall()
            ids.update(row["youtube_source_id"] for row in rows)
        except Exception:
            pass  # Table may not exist yet

        # Videos already recommended in past runs
        try:
            rows = self._conn.execute("""
                SELECT DISTINCT youtube_video_id FROM discovery_recommendations
            """).fetchall()
            ids.update(row["youtube_video_id"] for row in rows)
        except Exception:
            pass  # Table may not exist yet

        return ids
