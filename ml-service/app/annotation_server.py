"""Lightweight HTTP server for subtitle annotation.

Go subtitle pipeline calls POST /annotate after translation.
Returns persona-voiced annotation entries to merge into BCC.

Usage:
    python -m app.annotation_server --port 8082 --persona sarcastic_ai --backend ollama
"""
import json
import logging
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

os.environ.setdefault("PYTHONUTF8", "1")

from app.llm import create_backend
from app.personas._shared.subtitle_annotator import (
    generate_annotations,
    annotations_to_bcc_entries,
)

logger = logging.getLogger(__name__)

# Global state set by main()
_backend = None
_persona_prompt = ""


class AnnotationHandler(BaseHTTPRequestHandler):
    """Handles POST /annotate requests from Go subtitle pipeline."""

    def do_POST(self):
        if self.path != "/annotate":
            self.send_error(404, "Not found")
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            req = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        srt_content = req.get("srt_content", "")
        video_title = req.get("video_title", "")
        max_annotations = req.get("max_annotations", 0)  # 0 = auto-scale by duration

        if not srt_content:
            self.send_error(400, "srt_content is required")
            return

        try:
            annotations = generate_annotations(
                backend=_backend,
                srt_content=srt_content,
                persona_prompt=_persona_prompt,
                video_title=video_title,
                max_annotations=max_annotations,
            )
            entries = annotations_to_bcc_entries(annotations)
        except Exception as e:
            logger.error("Annotation failed: %s", e, exc_info=True)
            # Return empty annotations on failure (non-fatal)
            entries = []

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({
            "annotations": entries,
            "count": len(entries),
        }, ensure_ascii=False).encode("utf-8"))

    def log_message(self, format, *args):
        logger.info(format, *args)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Subtitle annotation server")
    parser.add_argument("--port", type=int, default=8082)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--persona", default="sarcastic_ai")
    parser.add_argument("--backend", default="ollama")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    global _backend, _persona_prompt
    os.environ.setdefault("LLM_BACKEND", args.backend)
    _backend = create_backend(backend_type=args.backend)

    # Load persona prompt
    if args.persona == "sarcastic_ai":
        from app.personas.sarcastic_ai.prompts import SYSTEM_PROMPT
        _persona_prompt = SYSTEM_PROMPT
    else:
        _persona_prompt = "你是一个有趣的AI评论员。"

    server = HTTPServer((args.host, args.port), AnnotationHandler)
    logger.info("Annotation server listening on %s:%d (persona=%s)", args.host, args.port, args.persona)
    server.serve_forever()


if __name__ == "__main__":
    main()
