#!/usr/bin/env python3
"""One-shot Whisper transcription script. Called by Go as a subprocess.

Usage:
    python3 whisper_transcribe.py <audio_or_video_path> [--model base]

Outputs SRT to stdout. Logs to stderr.
"""
import argparse
import sys
import time


def main():
    parser = argparse.ArgumentParser(description="Transcribe audio to SRT via faster-whisper")
    parser.add_argument("path", help="Path to audio or video file")
    parser.add_argument("--model", default="base", help="Whisper model size (default: base)")
    args = parser.parse_args()

    from faster_whisper import WhisperModel

    print(f"Loading model '{args.model}'...", file=sys.stderr)
    t0 = time.time()
    model = WhisperModel(args.model, compute_type="int8")
    print(f"Model loaded in {time.time() - t0:.1f}s", file=sys.stderr)

    print(f"Transcribing {args.path}...", file=sys.stderr)
    t0 = time.time()
    segments, info = model.transcribe(args.path, language="en")

    index = 0
    for s in segments:
        text = s.text.strip()
        if not text:
            continue
        index += 1
        start = _fmt_ts(s.start)
        end = _fmt_ts(s.end)
        print(index)
        print(f"{start} --> {end}")
        print(text)
        print()

    print(f"Done: {index} segments in {time.time() - t0:.1f}s "
          f"(lang={info.language}, prob={info.language_probability:.2f})", file=sys.stderr)


def _fmt_ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")


if __name__ == "__main__":
    main()
