"""FastAPI server wrapping the persona pipeline.

Run: uvicorn app.server:app --host 0.0.0.0 --port 8000 --reload
"""
import asyncio
import json
import logging
import os
import subprocess
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from enum import Enum

import httpx
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.responses import Response, StreamingResponse

os.environ.setdefault("PYTHONUTF8", "1")

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

# ── Config ─────────────────────────────────────────────────────
# Project root: parent of ml-service/
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_ML_SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_DATA_DB = os.path.join(_ML_SERVICE_DIR, "data.db")

GO_URL = os.environ.get("GO_URL", "http://localhost:8081")
GO_BINARY = os.environ.get("GO_BINARY", os.path.join(_PROJECT_ROOT, "yttransfer.exe"))
GO_HTTP_ADDR = os.environ.get("GO_HTTP_ADDR", ":8081")
BILIUP_BINARY = os.environ.get(
    "BILIUP_BINARY",
    os.path.join(_PROJECT_ROOT, "ml-service", ".venv", "Scripts", "biliup.exe"),
)
BILIUP_COOKIE = os.environ.get(
    "BILIUP_COOKIE",
    os.path.join(_PROJECT_ROOT, "scripts", "cookies.json"),
)

# ── Models ──────────────────────────────────────────────────────

class PipelineStep(str, Enum):
    idle = "idle"
    strategy_generation = "strategy_generation"
    search = "search"
    scoring = "scoring"
    copy_generation = "copy_generation"
    review = "review"
    uploading = "uploading"
    done = "done"
    error = "error"


class StartRequest(BaseModel):
    backend: str = "ollama"
    persona: str | None = None
    dry_run: bool = False
    no_upload: bool = False
    quota_budget: int = 2000


class ReviewDecision(BaseModel):
    approved: bool
    feedback: str | None = None


class CandidateOut(BaseModel):
    id: str
    video_id: str
    title: str
    title_zh: str
    description: str
    channel: str
    strategy: str
    score: float
    tsundere_score: int
    views: int
    duration_seconds: int
    approved: bool | None = None


class StatusOut(BaseModel):
    step: PipelineStep
    candidates: list[CandidateOut] = Field(default_factory=list)
    error: str | None = None
    summary: dict | None = None


class StrategySelection(BaseModel):
    names: list[str]


class ReflectRequest(BaseModel):
    skill_name: str = "strategy_generation"
    reflection_type: str = "yield"  # "yield" or "outcome"


class SkillUpdateRequest(BaseModel):
    system_prompt: str | None = None
    prompt_template: str | None = None


class RollbackRequest(BaseModel):
    target_version: int


class AnnotationFeedback(BaseModel):
    video_title: str
    kept: list[dict] = Field(default_factory=list)
    deleted: list[dict] = Field(default_factory=list)
    edited: list[dict] = Field(default_factory=list)


# ── Pipeline Session ───────────────────────────────────────────

class PipelineSession:
    def __init__(self):
        self.session_id: str = str(uuid.uuid4())
        self.phase: PipelineStep = PipelineStep.idle
        self.candidates: list[dict] = []
        self.error: str | None = None
        self.summary: dict | None = None
        self.available_strategies: list[dict] = []
        self.selected_strategies: set[str] = set()
        self.created_at: datetime = datetime.now()
        self._running: bool = False

        self._update_event: asyncio.Event = asyncio.Event()
        self._strategy_event: asyncio.Event = asyncio.Event()
        self._review_event: asyncio.Event = asyncio.Event()

    def notify(self, phase: PipelineStep, **data):
        self.phase = phase
        for k, v in data.items():
            setattr(self, k, v)
        self._update_event.set()
        self._update_event = asyncio.Event()  # reset for next wait

    async def wait_for_update(self) -> None:
        await self._update_event.wait()

    def to_status_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "step": self.phase.value,
            "candidates": [_format_single_candidate(j).model_dump() for j in self.candidates],
            "error": self.error,
            "summary": self.summary,
        }


sessions: dict[str, PipelineSession] = {}


# ── Review snapshot helpers ────────────────────────────────────

def _serialize_candidate(job: dict) -> dict:
    """Strip non-serializable fields (closures, candidate objects) from a job dict."""
    out = {}
    for k, v in job.items():
        if k.startswith("_") and k not in ("_id", "_approved", "_feedback"):
            continue
        if k == "candidate":
            c = v
            if c is not None:
                out["_candidate_data"] = {
                    "video_id": getattr(c, "video_id", ""),
                    "title": getattr(c, "title", ""),
                    "channel": getattr(c, "channel", getattr(c, "channel_title", "")),
                    "views": getattr(c, "views", 0),
                    "duration_seconds": getattr(c, "duration_seconds", 0),
                    "source_strategies": getattr(c, "source_strategies", []),
                }
            continue
        out[k] = v
    return out


def _deserialize_candidate(data: dict) -> dict:
    """Restore a serialized candidate dict. The 'candidate' object becomes a SimpleNamespace."""
    from types import SimpleNamespace
    out = dict(data)
    if "_candidate_data" in out:
        cd = out.pop("_candidate_data")
        out["candidate"] = SimpleNamespace(**cd)
    else:
        out["candidate"] = None
    # _regenerate_fn is lost after restart
    out["_regenerate_fn"] = None
    return out


# ── LLM concurrency control ───────────────────────────────────

llm_semaphore = asyncio.Semaphore(int(os.environ.get("LLM_CONCURRENCY", "2")))


async def call_llm(backend, messages, **kwargs):
    async with llm_semaphore:
        return await asyncio.to_thread(backend.chat, messages=messages, **kwargs)


# ── Helpers ─────────────────────────────────────────────────────

def _get_db():
    from app.db import Database
    db = Database(_DATA_DB)
    db.connect()
    db.ensure_all_tables()
    return db


def _format_candidates(jobs: list[dict]) -> list[CandidateOut]:
    return [_format_single_candidate(j) for j in jobs]


def _format_single_candidate(j: dict) -> CandidateOut:
    c = j.get("candidate")
    return CandidateOut(
        id=j["_id"],
        video_id=j["video_id"],
        title=j.get("title", ""),
        title_zh=j.get("title", ""),  # title IS the Chinese title from copy gen
        description=j.get("description", ""),
        channel=c.channel if c else "",
        strategy=j.get("strategy", ""),
        score=j.get("_rank_score", 0),
        tsundere_score=j.get("tsundere_score", 5),
        views=c.views if c else 0,
        duration_seconds=c.duration_seconds if c else 0,
        approved=j.get("_approved"),
    )


# ── App + Lifespan ─────────────────────────────────────────────

async def _session_cleanup_task():
    """Remove done/error sessions older than 1 hour, every 5 minutes."""
    while True:
        await asyncio.sleep(300)
        now = datetime.now()
        to_remove = []
        for sid, session in sessions.items():
            if session.phase in (PipelineStep.done, PipelineStep.error):
                age = (now - session.created_at).total_seconds()
                if age > 3600:
                    to_remove.append(sid)
        for sid in to_remove:
            del sessions[sid]
            logger.info("Cleaned up session %s", sid)


def _start_go(go_cmd: list[str]) -> subprocess.Popen | None:
    """Start the Go binary, return Popen or None if not found.

    cwd is set to _PROJECT_ROOT so Go's relative paths (ml-service/.venv/...,
    scripts/...) resolve correctly regardless of where Python was started.
    """
    try:
        proc = subprocess.Popen(go_cmd, cwd=_PROJECT_ROOT)
        logger.info("Started Go service (PID %d, cwd=%s): %s", proc.pid, _PROJECT_ROOT, go_cmd)
        return proc
    except FileNotFoundError:
        logger.warning("Go binary not found at %s — Go proxy endpoints will return 503", go_cmd[0])
        return None


async def _go_watchdog_task(go_cmd: list[str]):
    """Restart Go if it crashes. Check every 10 seconds."""
    while True:
        await asyncio.sleep(10)
        proc = app.state.go_process
        if proc is not None and proc.poll() is not None:
            logger.warning("Go service died (exit %s), restarting...", proc.returncode)
            app.state.go_process = _start_go(go_cmd)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start Go binary
    go_db_path = os.path.join(_PROJECT_ROOT, "metadata.db")
    ml_service_dir = os.path.join(_PROJECT_ROOT, "ml-service")
    go_cmd = [GO_BINARY, "--http-addr", GO_HTTP_ADDR, "--db-path", go_db_path]
    if BILIUP_BINARY and os.path.isfile(BILIUP_BINARY):
        go_cmd.extend(["--biliup-binary", BILIUP_BINARY])
    if BILIUP_COOKIE and os.path.isfile(BILIUP_COOKIE):
        go_cmd.extend(["--biliup-cookie", BILIUP_COOKIE])
    # Enable annotation generation (danmaku) via Go subprocess
    if os.path.isdir(ml_service_dir):
        go_cmd.extend(["--ml-service-dir", ml_service_dir])
    llm_backend = os.environ.get("LLM_BACKEND", "ollama")
    go_cmd.extend(["--llm-backend", llm_backend])

    app.state.go_process = _start_go(go_cmd)

    # Create shared httpx client
    app.state.http_client = httpx.AsyncClient(timeout=60.0)

    # Recover review snapshots from previous crash
    try:
        from app.db import Database
        rdb = Database(_DATA_DB)
        rdb.connect()
        rdb.ensure_all_tables()
        rows = rdb._conn.execute(
            "SELECT session_id, candidates_json FROM review_snapshots"
        ).fetchall()
        for row in rows:
            sid = row["session_id"]
            candidates = [
                _deserialize_candidate(c)
                for c in json.loads(row["candidates_json"])
            ]
            session = PipelineSession()
            session.session_id = sid
            session.phase = PipelineStep.review
            session.candidates = candidates
            session._running = False  # not actively running — just waiting for review
            sessions[sid] = session
            logger.info("Recovered review session %s with %d candidates", sid, len(candidates))
        rdb.close()
    except Exception as e:
        logger.warning("Failed to recover review snapshots: %s", e)

    # Start background tasks
    cleanup_task = asyncio.create_task(_session_cleanup_task())
    watchdog_task = asyncio.create_task(_go_watchdog_task(go_cmd))

    yield

    # Shutdown
    cleanup_task.cancel()
    watchdog_task.cancel()

    await app.state.http_client.aclose()

    go_proc = app.state.go_process
    if go_proc and go_proc.poll() is None:
        go_proc.terminate()
        try:
            go_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            go_proc.kill()
        logger.info("Stopped Go service")


app = FastAPI(title="YT Transport Pipeline", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

router = APIRouter(prefix="/api")


# ── Go Proxy Endpoints ─────────────────────────────────────────

async def _proxy_go(method: str, path: str, **kwargs) -> Response:
    """Proxy a request to the Go service."""
    client: httpx.AsyncClient = app.state.http_client
    url = f"{GO_URL}{path}"
    try:
        resp = await client.request(method, url, **kwargs)
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers={"content-type": resp.headers.get("content-type", "application/json")},
        )
    except httpx.ConnectError:
        return Response(
            content=json.dumps({"error": "Go service unavailable"}).encode(),
            status_code=503,
            headers={"content-type": "application/json"},
        )


@router.get("/uploads")
async def proxy_uploads(limit: int = 50):
    return await _proxy_go("GET", "/upload/jobs", params={"limit": limit})


@router.get("/uploads/{id}/status")
async def proxy_upload_status(id: int):
    return await _proxy_go("GET", "/upload/status", params={"id": id})


@router.get("/uploads/uploaded-ids")
async def proxy_uploaded_ids():
    return await _proxy_go("GET", "/upload/uploaded-ids")


@router.post("/uploads")
async def proxy_create_upload(body: dict):
    return await _proxy_go("POST", "/upload", json=body)


@router.delete("/uploads/{id}")
async def proxy_delete_upload(id: int):
    return await _proxy_go("DELETE", "/upload/job", params={"id": id})


@router.post("/uploads/{id}/retry-subtitle")
async def proxy_retry_subtitle(id: int):
    return await _proxy_go("POST", "/upload/retry-subtitle", params={"id": id})


@router.get("/uploads/{id}/subtitle-preview")
async def proxy_subtitle_preview(id: int):
    return await _proxy_go("GET", "/upload/subtitle-preview", params={"id": id})


@router.post("/uploads/{id}/approve-subtitle")
async def proxy_approve_subtitle(id: int):
    return await _proxy_go("POST", "/upload/subtitle-approve", params={"id": id})


@router.post("/uploads/{id}/annotate")
async def proxy_annotate(id: int):
    return await _proxy_go("POST", "/upload/annotate", params={"id": id})


# ── Pipeline Session Endpoints ─────────────────────────────────

@router.post("/pipeline/start")
async def start_pipeline(req: StartRequest):
    session = PipelineSession()
    session._running = True
    sessions[session.session_id] = session
    asyncio.create_task(run_pipeline(session, req))
    return {"session_id": session.session_id}


@router.get("/pipeline/{session_id}/status")
async def get_session_status(session_id: str):
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return session.to_status_dict()


@router.get("/pipeline/{session_id}/events")
async def pipeline_events(session_id: str):
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    async def event_stream():
        # Send current state immediately
        yield f"data: {json.dumps(session.to_status_dict())}\n\n"

        while True:
            try:
                # Wait for update with timeout for keepalive
                await asyncio.wait_for(session.wait_for_update(), timeout=30)
                yield f"data: {json.dumps(session.to_status_dict())}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"

            # Stop streaming when done or error
            if session.phase in (PipelineStep.done, PipelineStep.error):
                yield f"data: {json.dumps(session.to_status_dict())}\n\n"
                break

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/pipeline/{session_id}/strategies")
async def get_session_strategies(session_id: str):
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return session.available_strategies or []


@router.post("/pipeline/{session_id}/select-strategies")
async def select_session_strategies(session_id: str, body: StrategySelection):
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    session.selected_strategies = set(body.names)
    session._strategy_event.set()
    return {"status": "ok"}


@router.post("/pipeline/{session_id}/review/{candidate_id}")
async def review_session_candidate(session_id: str, candidate_id: str, decision: ReviewDecision):
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if session.phase != PipelineStep.review:
        raise HTTPException(400, "Not in review phase")

    job = None
    for c in session.candidates:
        if c["_id"] == candidate_id:
            job = c
            break
    if not job:
        raise HTTPException(404, "Candidate not found")

    job["_approved"] = decision.approved
    job["_feedback"] = decision.feedback

    # Check if all reviewed
    all_decided = all(c.get("_approved") is not None for c in session.candidates)
    if all_decided:
        session._review_event.set()

    # Notify SSE subscribers of the review update
    session.notify(session.phase)

    return {"status": "ok", "all_decided": all_decided}


@router.post("/pipeline/{session_id}/regenerate/{candidate_id}")
async def regenerate_session_candidate(session_id: str, candidate_id: str, decision: ReviewDecision):
    """Request LLM regeneration for a candidate with feedback."""
    session = sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if session.phase != PipelineStep.review:
        raise HTTPException(400, "Not in review phase")

    job = None
    for c in session.candidates:
        if c["_id"] == candidate_id:
            job = c
            break
    if not job:
        raise HTTPException(404, "Candidate not found")

    if not decision.feedback:
        raise HTTPException(400, "Feedback required for regeneration")

    regenerate_fn = job.get("_regenerate_fn")
    if not regenerate_fn:
        raise HTTPException(500, "Regeneration not available")

    try:
        title, desc, tsundere = await asyncio.to_thread(
            regenerate_fn, job, decision.feedback, job["title"], job["description"],
        )
        job["title"] = title
        job["description"] = desc
        job["tsundere_score"] = tsundere
    except Exception as e:
        raise HTTPException(500, f"Regeneration failed: {e}")

    # Notify SSE subscribers
    session.notify(session.phase)

    return _format_single_candidate(job)


@router.get("/sessions")
async def list_sessions():
    return [
        {
            "session_id": s.session_id,
            "phase": s.phase.value,
            "created_at": s.created_at.isoformat(),
            "running": s._running,
        }
        for s in sessions.values()
    ]


@router.delete("/pipeline/{session_id}")
async def delete_session(session_id: str):
    session = sessions.pop(session_id, None)
    if not session:
        raise HTTPException(404, "Session not found")
    return {"status": "ok"}


# ── Loop 2 & Skill Evolution endpoints ─────────────────────────

@router.post("/loop2/collect")
def collect_loop2():
    from app.loop2_collector import collect_loop2_data
    db = _get_db()
    try:
        result = collect_loop2_data(db, go_url=GO_URL)
        return result
    finally:
        db.close()


@router.get("/loop2/stats")
def loop2_stats():
    """Return all strategy_runs that have Bilibili outcome data."""
    db = _get_db()
    try:
        if not db._conn:
            raise HTTPException(500, "DB not connected")
        rows = db._conn.execute("""
            SELECT sr.*, s.name as strategy_name
            FROM strategy_runs sr
            LEFT JOIN strategies s ON sr.strategy_id = s.id
            WHERE sr.was_transported = 1 OR sr.bilibili_bvid IS NOT NULL
            ORDER BY sr.id DESC
            LIMIT 100
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


@router.get("/skills")
def list_skills():
    db = _get_db()
    try:
        if not db._conn:
            raise HTTPException(500, "DB not connected")
        rows = db._conn.execute("""
            SELECT name, version, system_prompt, prompt_template, output_schema
            FROM skills ORDER BY name
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


@router.get("/skills/{name}/versions")
def skill_versions(name: str):
    db = _get_db()
    try:
        versions = db.get_skill_versions(name)
        return versions
    finally:
        db.close()


@router.post("/skills/reflect")
def trigger_reflection(req: ReflectRequest):
    from app.llm import create_backend
    from app.skills import StrategyGenerationSkill, AnnotationSkill

    db = _get_db()
    try:
        backend_type = os.environ.get("LLM_BACKEND", "ollama")
        backend = create_backend(backend_type=backend_type)
        pid = "sarcastic_ai"

        if req.skill_name == "strategy_generation":
            skill = StrategyGenerationSkill(db=db, backend=backend)
            if req.reflection_type == "yield":
                yield_data = db.get_latest_run_yields(limit=50, persona_id=pid)
                strategy_stats = db.get_strategy_yield_stats(persona_id=pid)
                review_stats = db.get_review_stats_by_strategy(persona_id=pid)
                result = skill.reflect_on_yield(yield_data, strategy_stats, review_stats)
            else:
                # outcome reflection
                if not db._conn:
                    raise HTTPException(500, "DB not connected")
                outcomes = db._conn.execute("""
                    SELECT sr.*, s.name as strategy_name
                    FROM strategy_runs sr
                    JOIN strategies s ON sr.strategy_id = s.id
                    WHERE sr.bilibili_views IS NOT NULL AND sr.outcome IS NOT NULL
                    ORDER BY sr.id DESC LIMIT 50
                """).fetchall()
                result = skill.reflect_on_outcomes([dict(r) for r in outcomes])
        elif req.skill_name == "annotation":
            skill = AnnotationSkill(db=db, backend=backend)
            # Load accumulated feedback
            if not db._conn:
                raise HTTPException(500, "DB not connected")
            rows = db._conn.execute("""
                SELECT feedback_json FROM annotation_feedback
                ORDER BY id DESC LIMIT 20
            """).fetchall()
            feedback = [json.loads(r["feedback_json"]) for r in rows]
            result = skill.reflect_on_feedback(feedback)
        else:
            raise HTTPException(400, f"Unknown skill: {req.skill_name}")

        return {
            "result": result,
            "skill_name": req.skill_name,
            "current_version": skill.version,
        }
    finally:
        db.close()


@router.post("/skills/{name}/apply-reflection")
def apply_reflection(name: str, body: dict):
    """Apply a proposed reflection -- saves the new system prompt to DB."""
    proposed_prompt = body.get("system_prompt")
    reason = body.get("reason", "Human-approved reflection")
    if not proposed_prompt:
        raise HTTPException(400, "system_prompt required")

    db = _get_db()
    try:
        from app.llm import create_backend
        from app.skills import StrategyGenerationSkill, AnnotationSkill

        backend = create_backend(backend_type=os.environ.get("LLM_BACKEND", "ollama"))

        if name == "strategy_generation":
            skill = StrategyGenerationSkill(db=db, backend=backend)
        elif name == "annotation":
            skill = AnnotationSkill(db=db, backend=backend)
        else:
            raise HTTPException(400, f"Unknown skill: {name}")

        skill._update_prompt(
            {"system_prompt": proposed_prompt},
            changed_by="human_approved_reflection",
            reason=reason,
        )
        return {"status": "ok", "new_version": skill.version}
    finally:
        db.close()


@router.get("/review-stats")
def get_review_stats():
    db = _get_db()
    try:
        pid = "sarcastic_ai"
        stats = db.get_review_stats_by_strategy(persona_id=pid)
        return stats
    finally:
        db.close()


@router.post("/skills/{name}/rollback")
def rollback_skill(name: str, req: RollbackRequest):
    from app.llm import create_backend
    from app.skills import StrategyGenerationSkill, AnnotationSkill

    db = _get_db()
    try:
        backend = create_backend(backend_type=os.environ.get("LLM_BACKEND", "ollama"))
        skill_map = {
            "strategy_generation": StrategyGenerationSkill,
            "annotation": AnnotationSkill,
        }
        cls = skill_map.get(name)
        if not cls:
            raise HTTPException(400, f"Unknown skill: {name}")
        skill = cls(db=db, backend=backend)
        if not skill.rollback(req.target_version):
            raise HTTPException(404, f"Version {req.target_version} not found")
        return {"status": "ok", "new_version": skill.version}
    finally:
        db.close()


@router.post("/skills/{name}/update")
def update_skill(name: str, req: SkillUpdateRequest):
    from app.llm import create_backend
    from app.skills import StrategyGenerationSkill, AnnotationSkill

    db = _get_db()
    try:
        backend = create_backend(backend_type=os.environ.get("LLM_BACKEND", "ollama"))
        skill_map = {
            "strategy_generation": StrategyGenerationSkill,
            "annotation": AnnotationSkill,
        }
        cls = skill_map.get(name)
        if not cls:
            raise HTTPException(400, f"Unknown skill: {name}")
        skill = cls(db=db, backend=backend)
        updates = {}
        if req.system_prompt is not None:
            updates["system_prompt"] = req.system_prompt
        if req.prompt_template is not None:
            updates["prompt_template"] = req.prompt_template
        if not updates:
            raise HTTPException(400, "No updates provided")
        skill._update_prompt(updates, changed_by="manual", reason="User edit from frontend")
        return {"status": "ok", "new_version": skill.version}
    finally:
        db.close()


@router.post("/annotation/feedback")
def submit_annotation_feedback(feedback: AnnotationFeedback):
    """Store annotation feedback for future reflection."""
    db = _get_db()
    try:
        if not db._conn:
            raise HTTPException(500, "DB not connected")
        # Ensure feedback table exists
        db._conn.execute("""
            CREATE TABLE IF NOT EXISTS annotation_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                feedback_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        db._conn.execute(
            "INSERT INTO annotation_feedback (feedback_json) VALUES (?)",
            (json.dumps(feedback.model_dump(), ensure_ascii=False),),
        )
        db._conn.commit()
        return {"status": "ok"}
    finally:
        db.close()


# ── Pipeline runner (async) ────────────────────────────────────

async def run_pipeline(session: PipelineSession, req: StartRequest):
    """Run the persona pipeline, pausing at review for frontend input."""
    try:
        os.environ["LLM_BACKEND"] = req.backend

        from app.db import Database
        from app.personas import PersonaOrchestrator, ALL_PERSONAS
        from app.personas.protocol import RunContext

        import sqlite3 as _sqlite3
        db = Database(_DATA_DB)
        # Connect with check_same_thread=False so asyncio.to_thread workers
        # can use this connection (pipeline is the sole writer, serialized).
        db._conn = _sqlite3.connect(db.connection_string, check_same_thread=False)
        db._conn.row_factory = _sqlite3.Row
        db.ensure_all_tables()

        # Select persona
        if req.persona:
            persona_map = {cls().persona_id: cls for cls in ALL_PERSONAS}
            if req.persona not in persona_map:
                session.error = f"Unknown persona: {req.persona}"
                session.notify(PipelineStep.error)
                return
            persona_cls = persona_map[req.persona]
        else:
            persona_cls = list(ALL_PERSONAS)[0]

        persona = persona_cls()

        from app.llm import create_backend
        from app.personas._shared.transportability import check_transportability
        from app.personas._shared.scoring import ScoringParams, heuristic_score
        from app.personas._shared.aggregator import SearchAggregator
        from app.personas._shared.outcomes import OutcomeTracker
        from app.personas._shared.upload import submit_upload, get_uploaded_ids
        from app.personas._shared.tags import generate_tags
        from app.skills import StrategyGenerationSkill

        backend = create_backend(backend_type=req.backend)
        tracker = OutcomeTracker(db)

        # Import persona-specific config
        from app.personas.sarcastic_ai.config import (
            PERSONA_ID, SEARCH_IDENTITY, CONTENT_AFFINITY,
            PERSONA_FIT_PROMPT, PERSONA_FIT_THRESHOLD,
        )
        from app.personas.sarcastic_ai.prompts import (
            SYSTEM_PROMPT, STRATEGY_HINTS, sample_few_shot, get_temperature,
        )
        from app.personas.sarcastic_ai.strategies import (
            bootstrap_strategies, bootstrap_scoring, validate_query, validate_result,
        )
        from app.personas.sarcastic_ai import (
            _build_dynamic_examples, _pick_dynamic_example,
            _parse_copy_response, _rank_and_select, _make_regenerate_fn,
        )

        pid = persona.persona_id
        await asyncio.to_thread(bootstrap_strategies, db, persona_id=pid)
        await asyncio.to_thread(bootstrap_scoring, db, persona_id=pid)

        # ── Phase 1 ──
        session.notify(PipelineStep.strategy_generation)
        strategies = await asyncio.to_thread(db.list_strategies, active_only=True, persona_id=pid)
        strategy_skill = StrategyGenerationSkill(db=db, backend=backend)
        strategies_context = strategy_skill.format_strategies_context(strategies)
        recent_outcomes = strategy_skill.format_recent_outcomes(
            await asyncio.to_thread(db.get_latest_run_yields, limit=30, persona_id=pid)
        )
        gen_result = await asyncio.to_thread(strategy_skill.execute, {
            "strategies_with_full_context": strategies_context,
            "recent_outcomes_with_youtube_context": recent_outcomes,
            "hot_words": "(not available)",
        })
        queries = gen_result.get("queries", [])
        valid_queries = []
        for q in queries:
            qt = q.get("query", "") if isinstance(q, dict) else q
            sn = q.get("strategy_name", "unknown") if isinstance(q, dict) else "unknown"
            if validate_query(sn, qt):
                valid_queries.append(q)
        queries = valid_queries

        if not queries:
            session.error = "No queries generated"
            session.notify(PipelineStep.error)
            return

        from collections import defaultdict
        by_strategy = defaultdict(list)
        for q in queries:
            sn = q.get("strategy_name", "unknown") if isinstance(q, dict) else "unknown"
            by_strategy[sn].append(q)

        session.available_strategies = [
            {"name": name, "query_count": len(qs)}
            for name, qs in by_strategy.items()
        ]
        session.notify(PipelineStep.strategy_generation)
        session._strategy_event = asyncio.Event()
        await session._strategy_event.wait()

        selected = session.selected_strategies
        queries = [q for q in queries
                   if (q.get("strategy_name") if isinstance(q, dict) else "unknown") in selected]

        # ── Phase 2+3 ──
        session.notify(PipelineStep.search)
        from app.personas._shared.youtube import search_youtube_videos

        aggregator = SearchAggregator()
        already_seen = await asyncio.to_thread(db.get_already_transported_yt_ids)
        try:
            already_seen |= await asyncio.to_thread(get_uploaded_ids, GO_URL)
        except Exception:
            pass

        params_row = await asyncio.to_thread(db.get_scoring_params, persona_id=pid)
        scoring_params = ScoringParams.from_json(params_row["params_json"]) if params_row else ScoringParams()

        quota_used = 0
        for q in queries:
            if quota_used >= req.quota_budget:
                break
            qt = q.get("query", "") if isinstance(q, dict) else q
            sn = q.get("strategy_name", "unknown") if isinstance(q, dict) else "unknown"
            if not qt:
                continue

            strategy_row = await asyncio.to_thread(db.get_strategy, sn, persona_id=pid)
            strategy_id = strategy_row["id"] if strategy_row else None
            run_id = await asyncio.to_thread(
                db.save_strategy_run,
                strategy_id, qt, persona_id=pid,
                bilibili_check=q.get("bilibili_check") if isinstance(q, dict) else None,
            ) if strategy_id else None

            candidates = await asyncio.to_thread(search_youtube_videos, qt, max_results=10, max_age_days=90)
            quota_used += 100

            avg_views = sum(c.views for c in candidates) // len(candidates) if candidates else 0
            best = None

            for c in candidates:
                if c.video_id in already_seen:
                    continue
                if c.views < scoring_params.youtube_min_views:
                    continue
                if not validate_result(sn, c.title):
                    continue

                score = heuristic_score(c.views, c.likes, c.duration_seconds, c.category_id, 0.5, scoring_params)
                affinity = CONTENT_AFFINITY.get(c.category_id, 0.5)
                score *= affinity

                aggregator.add(
                    video_id=c.video_id, title=c.title, channel=c.channel_title,
                    views=c.views, likes=c.likes, duration_seconds=c.duration_seconds,
                    category_id=c.category_id, opportunity_score=score,
                    strategy=sn, query=qt,
                )
                already_seen.add(c.video_id)
                if best is None or c.views > best.get("views", 0):
                    best = {"id": c.video_id, "title": c.title, "channel": c.channel_title,
                            "views": c.views, "likes": c.likes, "category_id": c.category_id,
                            "duration_seconds": c.duration_seconds}

            if run_id:
                await asyncio.to_thread(tracker.record_query_yield, run_id, len(candidates), avg_views, best)

        # ── Phase 4 ──
        session.notify(PipelineStep.scoring)
        top_candidates = aggregator.get_candidates(min_views=scoring_params.youtube_min_views)[:20]
        approved = []
        rejected_count = 0

        for c in top_candidates:
            check = await asyncio.to_thread(
                check_transportability,
                backend=backend, title=c.title, channel=c.channel,
                duration_seconds=c.duration_seconds, category_id=c.category_id,
                persona_fit_prompt=PERSONA_FIT_PROMPT, persona_fit_threshold=PERSONA_FIT_THRESHOLD,
            )
            if check["transportable"]:
                approved.append((c, check))
            else:
                rejected_count += 1

        # ── Phase 5 ──
        session.notify(PipelineStep.copy_generation)
        upload_jobs = []
        dynamic_examples = await asyncio.to_thread(_build_dynamic_examples, db, pid)

        for candidate, check in approved:
            sn = candidate.source_strategies[0] if candidate.source_strategies else "unknown"
            hint = STRATEGY_HINTS.get(sn, "")
            static_examples = sample_few_shot(sn, count=2)
            temperature = get_temperature(sn)

            copy_prompt = (
                f"原标题：{candidate.title}\n"
                f"频道：{candidate.channel}\n"
                f"YouTube播放量：{candidate.views:,}次观看\n"
                f"时长：{candidate.duration_seconds // 60}分{candidate.duration_seconds % 60}秒\n"
                f"搜索策略：{sn}\n"
                f"策略提示：{hint}"
            )

            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            for ex in static_examples:
                messages.append({"role": "user", "content": ex["input"]})
                messages.append({"role": "assistant", "content": ex["output"]})
            dyn = _pick_dynamic_example(dynamic_examples, sn)
            if dyn:
                messages.append({"role": "user", "content": dyn["input"]})
                messages.append({"role": "assistant", "content": dyn["output"]})
            messages.append({"role": "user", "content": copy_prompt})

            response = await call_llm(backend, messages, temperature=temperature)
            title, desc, tsundere = _parse_copy_response(response)

            if not title:
                continue

            upload_jobs.append({
                "_id": candidate.video_id,
                "video_id": candidate.video_id,
                "title": title,
                "description": desc,
                "strategy": sn,
                "candidate": candidate,
                "tsundere_score": tsundere,
                "_approved": None,
                "_feedback": None,
                "_regenerate_fn": _make_regenerate_fn(backend),
            })

        if upload_jobs:
            upload_jobs = _rank_and_select(upload_jobs, top_n=3)

        # ── Phase 6: Review (PAUSE for frontend) ──
        session.candidates = upload_jobs
        session._review_event = asyncio.Event()

        # Save review snapshot for crash recovery
        try:
            snapshot_data = [_serialize_candidate(j) for j in upload_jobs]
            db._conn.execute(
                "INSERT INTO review_snapshots (session_id, run_id, candidates_json) VALUES (?, ?, ?)",
                (session.session_id, session.session_id, json.dumps(snapshot_data, ensure_ascii=False)),
            )
            db._conn.commit()
        except Exception as e:
            logger.warning("Failed to save review snapshot: %s", e)

        session.notify(PipelineStep.review)

        # Wait for all decisions from frontend (no timeout)
        await session._review_event.wait()

        # Delete snapshot — review completed
        try:
            db._conn.execute(
                "DELETE FROM review_snapshots WHERE session_id = ?",
                (session.session_id,),
            )
            db._conn.commit()
        except Exception as e:
            logger.warning("Failed to delete review snapshot: %s", e)

        # Filter to approved only
        approved_jobs = [j for j in session.candidates if j.get("_approved")]

        # Save review decisions to DB
        for job in session.candidates:
            decision = "approved" if job.get("_approved") else "rejected"
            await asyncio.to_thread(
                db.save_review_decision,
                persona_id=pid, strategy_run_id=None,
                youtube_video_id=job["video_id"],
                strategy_name=job.get("strategy", "unknown"),
                decision=decision,
                original_title=job["candidate"].title,
                original_desc="",
                final_title=job["title"],
                final_desc=job["description"],
            )

        # ── Phase 7: Upload ──
        if req.dry_run or req.no_upload:
            session.summary = {
                "discovered": aggregator.count(),
                "approved": len(approved_jobs),
                "rejected": rejected_count,
                "uploaded": 0,
                "skipped": "dry_run" if req.dry_run else "no_upload",
            }
        else:
            session.notify(PipelineStep.uploading)
            uploaded = 0
            for job in approved_jobs:
                tags = []
                try:
                    tags = await generate_tags(job["title"], max_tags=10)
                except Exception:
                    pass
                resp = await asyncio.to_thread(
                    submit_upload,
                    go_url=GO_URL, video_id=job["video_id"],
                    title=job["title"], description=job["description"],
                    tags=",".join(tags),
                    persona_id=pid, strategy_name=job.get("strategy", ""),
                )
                if resp.get("status") not in ("failed",):
                    uploaded += 1

            session.summary = {
                "discovered": aggregator.count(),
                "approved": len(approved_jobs),
                "rejected": rejected_count,
                "uploaded": uploaded,
            }

        # NOTE: Auto-reflection disabled -- pipeline only records data.
        # Reflection is triggered manually from the AI进化 tab with human approval.
        # Re-enable when the system is stable enough for autonomous prompt evolution.

        session.notify(PipelineStep.done)
        db.close()

    except Exception as e:
        logger.error("Pipeline error: %s", e, exc_info=True)
        session.error = str(e)
        session.notify(PipelineStep.error)
    finally:
        session._running = False


# ── Mount router ───────────────────────────────────────────────
app.include_router(router)
