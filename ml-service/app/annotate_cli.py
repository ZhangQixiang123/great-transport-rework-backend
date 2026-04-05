"""CLI entrypoint for subtitle annotation — called by Go as a subprocess.

Uses AnnotationSkill (self-improving prompts from DB) when available,
falls back to hardcoded prompts if DB is unavailable.

Usage:
    python -m app.annotate_cli --backend ollama < input.json > output.json

Input JSON (stdin):  {"srt_content": "...", "video_title": "...", "max_annotations": 0}
Output JSON (stdout): {"annotations": [...], "count": N}
"""
import json
import logging
import os
import sys

os.environ.setdefault("PYTHONUTF8", "1")

logger = logging.getLogger(__name__)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Generate subtitle annotations (CLI)")
    parser.add_argument("--backend", default="ollama")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    os.environ.setdefault("LLM_BACKEND", args.backend)

    from app.llm import create_backend
    from app.personas._shared.subtitle_annotator import (
        parse_srt_blocks,
        annotations_to_bcc_entries,
        Annotation,
    )

    backend = create_backend(backend_type=args.backend)

    # Read request from stdin
    req = json.loads(sys.stdin.read())
    srt_content = req.get("srt_content", "")
    video_title = req.get("video_title", "")
    max_annotations = req.get("max_annotations", 0)

    if not srt_content:
        json.dump({"annotations": [], "count": 0}, sys.stdout, ensure_ascii=False)
        return

    # Parse SRT to build context
    blocks = parse_srt_blocks(srt_content)
    if not blocks:
        json.dump({"annotations": [], "count": 0}, sys.stdout, ensure_ascii=False)
        return

    if len(blocks) > 30:
        step = len(blocks) / 30
        sampled = [blocks[int(i * step)] for i in range(30)]
    else:
        sampled = blocks

    subtitle_summary = "\n".join(
        f"[{b['from_sec']:.0f}s] {b['text']}" for b in sampled
    )
    total_duration = blocks[-1]["to_sec"] if blocks else 0

    if max_annotations <= 0:
        max_annotations = max(3, min(10, int(total_duration / 20)))

    # Try to use AnnotationSkill (evolving prompts from DB)
    entries = []
    try:
        from app.db import Database
        from app.skills.annotation import AnnotationSkill

        _db_path = os.path.join(os.path.dirname(__file__), "..", "data.db")
        db = Database(os.path.abspath(_db_path))
        db.connect()
        db.ensure_all_tables()

        skill = AnnotationSkill(db=db, backend=backend)
        result = skill.execute({
            "video_title": video_title,
            "total_duration": f"{total_duration:.0f}",
            "subtitle_summary": subtitle_summary,
            "max_annotations": str(max_annotations),
        })

        # Parse skill output into BCC entries
        raw_annotations = result.get("annotations", [])
        annotations = []
        for item in raw_annotations[:max_annotations]:
            if not isinstance(item, dict):
                continue
            time_sec = item.get("time", 0)
            comment = item.get("comment", "").strip()
            if not comment or not isinstance(time_sec, (int, float)):
                continue
            time_sec = max(0, min(time_sec, total_duration - 3))
            annotations.append(Annotation(
                from_sec=round(time_sec, 1),
                to_sec=round(time_sec + 3.0, 1),
                content=comment,
            ))

        entries = annotations_to_bcc_entries(annotations)
        logger.info("AnnotationSkill generated %d annotations (v%d)", len(entries), skill.version)
        db.close()

    except Exception as e:
        logger.warning("AnnotationSkill unavailable, falling back to hardcoded: %s", e)
        # Fallback to hardcoded prompts
        from app.personas._shared.subtitle_annotator import (
            generate_annotations,
            annotations_to_bcc_entries,
        )
        try:
            from app.personas.sarcastic_ai.prompts import SYSTEM_PROMPT
            persona_prompt = SYSTEM_PROMPT
        except Exception:
            persona_prompt = "你是一个有趣的AI评论员。"

        try:
            annotations = generate_annotations(
                backend=backend,
                srt_content=srt_content,
                persona_prompt=persona_prompt,
                video_title=video_title,
                max_annotations=max_annotations,
            )
            entries = annotations_to_bcc_entries(annotations)
        except Exception as e2:
            logger.error("Fallback annotation also failed: %s", e2)
            entries = []

    json.dump({"annotations": entries, "count": len(entries)}, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
