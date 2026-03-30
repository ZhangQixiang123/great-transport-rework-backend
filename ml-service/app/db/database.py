"""SQLite database adapter for the persona-centric pipeline."""
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional


class Database:
    """SQLite database adapter.

    Manages tables for:
    - Skills / skill versions (self-evolving prompts)
    - Strategies / strategy runs (discovery framework)
    - Followed channels / scoring params
    - Persona config KV store (NEW)
    - Search rounds (NEW)
    - Review decisions (NEW)
    - Persona analyses (NEW)
    """

    def __init__(self, connection_string: str):
        self.connection_string = connection_string
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        self._conn = sqlite3.connect(self.connection_string)
        self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # ── Table creation ────────────────────────────────────────────────────

    def ensure_skill_tables(self) -> None:
        """Create skill framework tables if they don't exist."""
        if not self._conn:
            raise RuntimeError("Database not connected")

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS skills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                system_prompt TEXT NOT NULL,
                prompt_template TEXT NOT NULL,
                output_schema TEXT NOT NULL,
                version INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS skill_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                skill_id INTEGER REFERENCES skills(id),
                version INTEGER NOT NULL,
                system_prompt TEXT NOT NULL,
                prompt_template TEXT NOT NULL,
                changed_by TEXT NOT NULL,
                change_reason TEXT,
                performance_before TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_skill_versions_skill
            ON skill_versions(skill_id, version)
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS strategies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                persona_id TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL,
                example_queries TEXT,
                failed_queries TEXT,
                youtube_channels TEXT,
                youtube_categories TEXT,
                search_tips TEXT,
                bilibili_check TEXT,
                audience_notes TEXT,
                source TEXT DEFAULT 'manual',
                total_queries INTEGER DEFAULT 0,
                yielded_queries INTEGER DEFAULT 0,
                yield_rate REAL DEFAULT 0.0,
                total_recommended INTEGER DEFAULT 0,
                total_transported INTEGER DEFAULT 0,
                successful_transports INTEGER DEFAULT 0,
                transport_success_rate REAL,
                avg_bilibili_views REAL,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                retired_at TIMESTAMP,
                UNIQUE(name, persona_id)
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS strategy_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                persona_id TEXT,
                strategy_id INTEGER REFERENCES strategies(id),
                query TEXT NOT NULL,
                query_result_count INTEGER,
                query_avg_views INTEGER,
                yield_success INTEGER DEFAULT 0,
                youtube_video_id TEXT,
                youtube_title TEXT,
                youtube_channel TEXT,
                youtube_channel_id TEXT,
                youtube_views INTEGER,
                youtube_likes INTEGER,
                youtube_category_id INTEGER,
                youtube_duration_seconds INTEGER,
                bilibili_check TEXT,
                bilibili_similar_count INTEGER,
                bilibili_novelty_score REAL,
                was_recommended INTEGER DEFAULT 0,
                was_transported INTEGER DEFAULT 0,
                bilibili_bvid TEXT,
                bilibili_views INTEGER,
                outcome TEXT,
                run_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                outcome_recorded_at TIMESTAMP
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_strategy_runs_strategy
            ON strategy_runs(strategy_id)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_strategy_runs_yt_id
            ON strategy_runs(youtube_video_id)
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS followed_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                youtube_channel_id TEXT,
                channel_name TEXT UNIQUE NOT NULL,
                reason TEXT,
                source TEXT DEFAULT 'manual',
                strategy_id INTEGER,
                transport_count INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                last_checked_at TIMESTAMP,
                is_active INTEGER DEFAULT 1,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (strategy_id) REFERENCES strategies(id)
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS scoring_params (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                persona_id TEXT NOT NULL DEFAULT '',
                params_json TEXT NOT NULL,
                source TEXT DEFAULT 'competitor',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._conn.commit()

    def ensure_persona_tables(self) -> None:
        """Create persona-specific tables (KV config, search rounds, review, historian)."""
        if not self._conn:
            raise RuntimeError("Database not connected")

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS persona_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                persona_id TEXT NOT NULL,
                config_key TEXT NOT NULL,
                config_json TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(persona_id, config_key)
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS search_rounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                persona_id TEXT NOT NULL,
                strategy_run_id INTEGER,
                round_number INTEGER,
                query TEXT,
                original_query TEXT,
                result_count INTEGER,
                avg_views INTEGER,
                quality_score REAL,
                was_refined INTEGER DEFAULT 0,
                quota_units_used INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS review_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                persona_id TEXT NOT NULL,
                strategy_run_id INTEGER,
                youtube_video_id TEXT NOT NULL,
                strategy_name TEXT,
                decision TEXT NOT NULL,
                original_title TEXT,
                original_desc TEXT,
                final_title TEXT,
                final_desc TEXT,
                feedback_rounds_json TEXT,
                reject_reason TEXT DEFAULT '',
                review_time_seconds REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS persona_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                persona_id TEXT NOT NULL,
                summary_json TEXT NOT NULL,
                total_runs_analyzed INTEGER,
                success_rate REAL,
                updates_applied TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._conn.commit()

    def ensure_all_tables(self) -> None:
        """Create all tables."""
        self.ensure_skill_tables()
        self.ensure_persona_tables()
        self._migrate_persona_id()

    def _migrate_persona_id(self) -> None:
        """Add persona_id columns to legacy tables if missing.

        Also rebuilds strategies table to use UNIQUE(name, persona_id)
        instead of the legacy UNIQUE(name).
        """
        if not self._conn:
            return
        for table, default in [("strategies", "''"), ("scoring_params", "''")]:
            try:
                self._conn.execute(f"SELECT persona_id FROM {table} LIMIT 1")
            except Exception:
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN persona_id TEXT NOT NULL DEFAULT {default}"
                )
                self._conn.commit()

        # Check if strategies has old UNIQUE(name) — rebuild if so.
        idx_info = self._conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='strategies'"
        ).fetchone()
        if idx_info and "UNIQUE(name, persona_id)" not in (idx_info["sql"] or ""):
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS strategies_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    persona_id TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL,
                    example_queries TEXT,
                    failed_queries TEXT,
                    youtube_channels TEXT,
                    youtube_categories TEXT,
                    search_tips TEXT,
                    bilibili_check TEXT,
                    audience_notes TEXT,
                    source TEXT DEFAULT 'manual',
                    total_queries INTEGER DEFAULT 0,
                    yielded_queries INTEGER DEFAULT 0,
                    yield_rate REAL DEFAULT 0.0,
                    total_recommended INTEGER DEFAULT 0,
                    total_transported INTEGER DEFAULT 0,
                    successful_transports INTEGER DEFAULT 0,
                    transport_success_rate REAL,
                    avg_bilibili_views REAL,
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    retired_at TIMESTAMP,
                    UNIQUE(name, persona_id)
                );
                INSERT OR IGNORE INTO strategies_new
                    SELECT id, name, persona_id, description,
                           example_queries, failed_queries,
                           youtube_channels, youtube_categories,
                           search_tips, bilibili_check, audience_notes,
                           source, total_queries, yielded_queries, yield_rate,
                           total_recommended, total_transported,
                           successful_transports, transport_success_rate,
                           avg_bilibili_views, is_active, created_at, retired_at
                    FROM strategies;
                DROP TABLE strategies;
                ALTER TABLE strategies_new RENAME TO strategies;
            """)

    # ── Persona config KV store ───────────────────────────────────────────

    def save_persona_kv(self, persona_id: str, key: str, json_str: str) -> None:
        """Upsert a persona config entry."""
        if not self._conn:
            raise RuntimeError("Database not connected")
        self._conn.execute("""
            INSERT INTO persona_config (persona_id, config_key, config_json, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(persona_id, config_key) DO UPDATE SET
                config_json = excluded.config_json,
                updated_at = CURRENT_TIMESTAMP
        """, (persona_id, key, json_str))
        self._conn.commit()

    def get_persona_kv(self, persona_id: str, key: str) -> Optional[str]:
        """Read a persona config entry. Returns JSON string or None."""
        if not self._conn:
            raise RuntimeError("Database not connected")
        row = self._conn.execute(
            "SELECT config_json FROM persona_config WHERE persona_id = ? AND config_key = ?",
            (persona_id, key),
        ).fetchone()
        return row["config_json"] if row else None

    # ── Search rounds ─────────────────────────────────────────────────────

    def save_search_round(
        self,
        persona_id: str,
        strategy_run_id: Optional[int],
        round_number: int,
        query: str,
        original_query: str,
        result_count: int,
        avg_views: int,
        quality_score: float,
        was_refined: bool,
        quota_units_used: int,
    ) -> int:
        if not self._conn:
            raise RuntimeError("Database not connected")
        cursor = self._conn.execute("""
            INSERT INTO search_rounds
                (persona_id, strategy_run_id, round_number, query, original_query,
                 result_count, avg_views, quality_score, was_refined, quota_units_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            persona_id, strategy_run_id, round_number, query, original_query,
            result_count, avg_views, quality_score, 1 if was_refined else 0,
            quota_units_used,
        ))
        self._conn.commit()
        return cursor.lastrowid

    # ── Review decisions ──────────────────────────────────────────────────

    def save_review_decision(
        self,
        persona_id: str,
        strategy_run_id: Optional[int],
        youtube_video_id: str,
        strategy_name: str,
        decision: str,
        original_title: str,
        original_desc: str,
        final_title: Optional[str] = None,
        final_desc: Optional[str] = None,
        feedback_rounds_json: Optional[str] = None,
        reject_reason: str = "",
        review_time_seconds: float = 0,
    ) -> int:
        if not self._conn:
            raise RuntimeError("Database not connected")
        cursor = self._conn.execute("""
            INSERT INTO review_decisions
                (persona_id, strategy_run_id, youtube_video_id, strategy_name,
                 decision, original_title, original_desc, final_title, final_desc,
                 feedback_rounds_json, reject_reason, review_time_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            persona_id, strategy_run_id, youtube_video_id, strategy_name,
            decision, original_title, original_desc, final_title, final_desc,
            feedback_rounds_json, reject_reason, review_time_seconds,
        ))
        self._conn.commit()
        return cursor.lastrowid

    def get_review_decisions(
        self, persona_id: str, limit: int = 100,
    ) -> List[Dict[str, Any]]:
        if not self._conn:
            raise RuntimeError("Database not connected")
        rows = self._conn.execute("""
            SELECT * FROM review_decisions
            WHERE persona_id = ?
            ORDER BY id DESC LIMIT ?
        """, (persona_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def get_approved_examples(
        self, persona_id: str, limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Get past approved/revised reviews as potential few-shot examples.

        Revised decisions (where user gave feedback) are prioritized because
        they represent human-corrected data — the gold standard for the persona voice.
        """
        if not self._conn:
            raise RuntimeError("Database not connected")
        rows = self._conn.execute("""
            SELECT original_title, final_title, final_desc,
                   strategy_name, decision, feedback_rounds_json
            FROM review_decisions
            WHERE persona_id = ?
              AND decision IN ('approved', 'revised')
              AND final_title IS NOT NULL
              AND final_title != ''
            ORDER BY
                CASE WHEN decision = 'revised' THEN 0 ELSE 1 END,
                id DESC
            LIMIT ?
        """, (persona_id, limit)).fetchall()
        return [dict(r) for r in rows]

    # ── Persona analyses ──────────────────────────────────────────────────

    def save_persona_analysis(
        self,
        persona_id: str,
        summary_json: str,
        total_runs_analyzed: int,
        success_rate: float,
        updates_applied: str,
    ) -> int:
        if not self._conn:
            raise RuntimeError("Database not connected")
        cursor = self._conn.execute("""
            INSERT INTO persona_analyses
                (persona_id, summary_json, total_runs_analyzed, success_rate, updates_applied)
            VALUES (?, ?, ?, ?, ?)
        """, (persona_id, summary_json, total_runs_analyzed, success_rate, updates_applied))
        self._conn.commit()
        return cursor.lastrowid

    # ── Skill CRUD ────────────────────────────────────────────────────────

    def get_skill(self, name: str) -> Optional[Dict[str, Any]]:
        if not self._conn:
            raise RuntimeError("Database not connected")
        row = self._conn.execute(
            "SELECT * FROM skills WHERE name = ?", (name,)
        ).fetchone()
        return dict(row) if row else None

    def upsert_skill(
        self, name: str, system_prompt: str, prompt_template: str,
        output_schema: str,
    ) -> int:
        if not self._conn:
            raise RuntimeError("Database not connected")
        self._conn.execute("""
            INSERT INTO skills (name, system_prompt, prompt_template, output_schema)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                system_prompt = excluded.system_prompt,
                prompt_template = excluded.prompt_template,
                output_schema = excluded.output_schema,
                updated_at = CURRENT_TIMESTAMP
        """, (name, system_prompt, prompt_template, output_schema))
        self._conn.commit()
        row = self._conn.execute(
            "SELECT id FROM skills WHERE name = ?", (name,)
        ).fetchone()
        return row["id"]

    def snapshot_skill_version(
        self, name: str, changed_by: str, reason: str,
        performance_before: Optional[str] = None,
    ) -> None:
        if not self._conn:
            raise RuntimeError("Database not connected")
        skill = self.get_skill(name)
        if not skill:
            return
        self._conn.execute("""
            INSERT INTO skill_versions
                (skill_id, version, system_prompt, prompt_template,
                 changed_by, change_reason, performance_before)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            skill["id"], skill["version"], skill["system_prompt"],
            skill["prompt_template"], changed_by, reason, performance_before,
        ))
        self._conn.commit()

    def get_skill_versions(self, name: str) -> List[Dict[str, Any]]:
        if not self._conn:
            raise RuntimeError("Database not connected")
        skill = self.get_skill(name)
        if not skill:
            return []
        rows = self._conn.execute("""
            SELECT * FROM skill_versions
            WHERE skill_id = ?
            ORDER BY version DESC
        """, (skill["id"],)).fetchall()
        return [dict(r) for r in rows]

    def update_skill_prompt(
        self, name: str, system_prompt: str, prompt_template: str,
    ) -> None:
        if not self._conn:
            raise RuntimeError("Database not connected")
        self._conn.execute("""
            UPDATE skills SET
                system_prompt = ?,
                prompt_template = ?,
                version = version + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE name = ?
        """, (system_prompt, prompt_template, name))
        self._conn.commit()

    # ── Strategy CRUD ─────────────────────────────────────────────────────

    def get_strategy(
        self, name: str, persona_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        if not self._conn:
            raise RuntimeError("Database not connected")
        row = self._conn.execute(
            "SELECT * FROM strategies WHERE name = ? AND persona_id = ?",
            (name, persona_id),
        ).fetchone()
        return dict(row) if row else None

    def list_strategies(
        self, active_only: bool = True, persona_id: str = "",
    ) -> List[Dict[str, Any]]:
        if not self._conn:
            raise RuntimeError("Database not connected")
        conditions = ["persona_id = ?"]
        params: list = [persona_id]
        if active_only:
            conditions.append("is_active = 1")
        query = "SELECT * FROM strategies WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at"
        rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def add_strategy(
        self, name: str, description: str,
        example_queries: Optional[str] = None,
        failed_queries: Optional[str] = None,
        youtube_channels: Optional[str] = None,
        youtube_categories: Optional[str] = None,
        search_tips: Optional[str] = None,
        bilibili_check: Optional[str] = None,
        audience_notes: Optional[str] = None,
        source: str = "manual",
        persona_id: str = "",
    ) -> int:
        if not self._conn:
            raise RuntimeError("Database not connected")
        cursor = self._conn.execute("""
            INSERT INTO strategies
                (name, persona_id, description, example_queries, failed_queries,
                 youtube_channels, youtube_categories, search_tips,
                 bilibili_check, audience_notes, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            name, persona_id, description, example_queries, failed_queries,
            youtube_channels, youtube_categories, search_tips,
            bilibili_check, audience_notes, source,
        ))
        self._conn.commit()
        return cursor.lastrowid

    def update_strategy_stats(
        self, strategy_id: int,
        total_queries: Optional[int] = None,
        yielded_queries: Optional[int] = None,
        total_recommended: Optional[int] = None,
        total_transported: Optional[int] = None,
        successful_transports: Optional[int] = None,
        avg_bilibili_views: Optional[float] = None,
    ) -> None:
        if not self._conn:
            raise RuntimeError("Database not connected")
        updates = []
        params: list = []
        if total_queries is not None:
            updates.append("total_queries = ?")
            params.append(total_queries)
        if yielded_queries is not None:
            updates.append("yielded_queries = ?")
            params.append(yielded_queries)
        if total_recommended is not None:
            updates.append("total_recommended = ?")
            params.append(total_recommended)
        if total_transported is not None:
            updates.append("total_transported = ?")
            params.append(total_transported)
        if successful_transports is not None:
            updates.append("successful_transports = ?")
            params.append(successful_transports)
        if avg_bilibili_views is not None:
            updates.append("avg_bilibili_views = ?")
            params.append(avg_bilibili_views)
        if total_queries is not None and yielded_queries is not None:
            rate = yielded_queries / max(total_queries, 1)
            updates.append("yield_rate = ?")
            params.append(rate)
        if total_transported is not None and successful_transports is not None:
            rate = successful_transports / max(total_transported, 1)
            updates.append("transport_success_rate = ?")
            params.append(rate)
        if not updates:
            return
        params.append(strategy_id)
        self._conn.execute(
            f"UPDATE strategies SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        self._conn.commit()

    def update_strategy_metadata(
        self, name: str, persona_id: str = "", **kwargs,
    ) -> None:
        """Update strategy metadata fields."""
        if not self._conn:
            raise RuntimeError("Database not connected")
        allowed = {
            "description", "example_queries", "failed_queries",
            "youtube_channels", "youtube_categories", "search_tips",
            "bilibili_check", "audience_notes",
        }
        updates = []
        params: list = []
        for k, v in kwargs.items():
            if k in allowed:
                updates.append(f"{k} = ?")
                params.append(v)
        if not updates:
            return
        params.extend([name, persona_id])
        self._conn.execute(
            f"UPDATE strategies SET {', '.join(updates)} WHERE name = ? AND persona_id = ?",
            params,
        )
        self._conn.commit()

    def retire_strategy(self, name: str, persona_id: str = "") -> None:
        if not self._conn:
            raise RuntimeError("Database not connected")
        self._conn.execute("""
            UPDATE strategies SET is_active = 0, retired_at = CURRENT_TIMESTAMP
            WHERE name = ? AND persona_id = ?
        """, (name, persona_id))
        self._conn.commit()

    # ── Strategy Run CRUD ─────────────────────────────────────────────────

    def save_strategy_run(
        self, strategy_id: int, query: str,
        persona_id: Optional[str] = None,
        bilibili_check: Optional[str] = None,
    ) -> int:
        if not self._conn:
            raise RuntimeError("Database not connected")
        cursor = self._conn.execute("""
            INSERT INTO strategy_runs (persona_id, strategy_id, query, bilibili_check)
            VALUES (?, ?, ?, ?)
        """, (persona_id, strategy_id, query, bilibili_check))
        self._conn.commit()
        return cursor.lastrowid

    def update_strategy_run(self, run_id: int, **kwargs) -> None:
        if not self._conn:
            raise RuntimeError("Database not connected")
        if not kwargs:
            return
        allowed = {
            "query_result_count", "query_avg_views", "yield_success",
            "youtube_video_id", "youtube_title", "youtube_channel",
            "youtube_channel_id", "youtube_views", "youtube_likes",
            "youtube_category_id", "youtube_duration_seconds",
            "bilibili_similar_count", "bilibili_novelty_score",
            "was_recommended", "was_transported", "bilibili_bvid",
            "bilibili_views", "outcome", "outcome_recorded_at",
            "persona_id",
        }
        updates = []
        params: list = []
        for k, v in kwargs.items():
            if k in allowed:
                updates.append(f"{k} = ?")
                params.append(v)
        if not updates:
            return
        params.append(run_id)
        self._conn.execute(
            f"UPDATE strategy_runs SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        self._conn.commit()

    def get_strategy_yield_stats(
        self, persona_id: str = "",
    ) -> List[Dict[str, Any]]:
        if not self._conn:
            raise RuntimeError("Database not connected")
        rows = self._conn.execute("""
            SELECT s.name, s.total_queries, s.yielded_queries, s.yield_rate
            FROM strategies s
            WHERE s.is_active = 1 AND s.persona_id = ?
            ORDER BY s.yield_rate DESC
        """, (persona_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_latest_run_yields(
        self, limit: int = 50, persona_id: str = "",
    ) -> List[Dict[str, Any]]:
        if not self._conn:
            raise RuntimeError("Database not connected")
        rows = self._conn.execute("""
            SELECT sr.*, s.name as strategy_name
            FROM strategy_runs sr
            JOIN strategies s ON sr.strategy_id = s.id
            WHERE s.persona_id = ?
            ORDER BY sr.run_at DESC
            LIMIT ?
        """, (persona_id, limit)).fetchall()
        return [dict(r) for r in rows]

    # ── Followed channels ─────────────────────────────────────────────────

    def add_followed_channel(
        self, channel_name: str,
        youtube_channel_id: Optional[str] = None,
        reason: Optional[str] = None,
        source: str = "manual",
        strategy_id: Optional[int] = None,
    ) -> int:
        if not self._conn:
            raise RuntimeError("Database not connected")
        cursor = self._conn.execute("""
            INSERT OR IGNORE INTO followed_channels
                (channel_name, youtube_channel_id, reason, source, strategy_id)
            VALUES (?, ?, ?, ?, ?)
        """, (channel_name, youtube_channel_id, reason, source, strategy_id))
        self._conn.commit()
        return cursor.lastrowid

    def list_followed_channels(self, active_only: bool = True) -> List[Dict[str, Any]]:
        if not self._conn:
            raise RuntimeError("Database not connected")
        query = "SELECT * FROM followed_channels"
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY added_at"
        rows = self._conn.execute(query).fetchall()
        return [dict(r) for r in rows]

    # ── Scoring params ────────────────────────────────────────────────────

    def save_scoring_params(
        self, params_json: str, source: str = "competitor",
        persona_id: str = "",
    ) -> None:
        if not self._conn:
            raise RuntimeError("Database not connected")
        self._conn.execute(
            "INSERT INTO scoring_params (persona_id, params_json, source) VALUES (?, ?, ?)",
            (persona_id, params_json, source),
        )
        self._conn.commit()

    def get_scoring_params(
        self, persona_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        if not self._conn:
            raise RuntimeError("Database not connected")
        row = self._conn.execute(
            "SELECT * FROM scoring_params WHERE persona_id = ? ORDER BY id DESC LIMIT 1",
            (persona_id,),
        ).fetchone()
        return dict(row) if row else None

    # ── Dedup helper ──────────────────────────────────────────────────────

    def get_already_transported_yt_ids(self) -> set:
        """Return YouTube video IDs that have already been transported."""
        if not self._conn:
            raise RuntimeError("Database not connected")
        ids: set[str] = set()
        try:
            rows = self._conn.execute("""
                SELECT DISTINCT youtube_video_id FROM strategy_runs
                WHERE youtube_video_id IS NOT NULL AND youtube_video_id != ''
            """).fetchall()
            ids.update(row["youtube_video_id"] for row in rows)
        except Exception:
            pass
        return ids
