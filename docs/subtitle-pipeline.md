# Subtitle Pipeline

Automatic Chinese CC subtitle + danmaku (弹幕) generation for uploaded Bilibili videos.

## Architecture

The subtitle pipeline runs within the Go service as a background goroutine after video upload completes. It calls Python as subprocess for transcription and annotation.

```
Go JobQueue (after video upload completes):
  1. Transcribe audio → English SRT    (whisper_autosrt, subprocess)
  2. Translate → Chinese SRT           (Google Translate HTTP, in Go)
  3. Generate annotations → danmaku    (python -m app.annotate_cli, subprocess)
  4. Save draft to DB for human review
```

## Pipeline Flow

```
POST /upload {video_id, title, description, tags}
  │
  ▼
JobQueue:
  ├─ Download video (yt-dlp)
  ├─ Upload to Bilibili (biliup) → BVID
  ├─ Mark job "completed"
  └─ Async subtitle goroutine:
      ├─ subtitle_status → "generating"
      ├─ whisper_autosrt → English SRT + Chinese SRT
      ├─ python -m app.annotate_cli → annotations JSON (LLM-generated danmaku)
      ├─ Save draft {english_srt, chinese_srt, annotations} to DB
      └─ subtitle_status → "review"
                │
          Human reviews via frontend:
          ├─ GET  /upload/subtitle-preview?id=N   (view draft)
          ├─ POST /upload/annotate?id=N           (regenerate annotations)
          └─ POST /upload/subtitle-approve?id=N   (publish to Bilibili)
                    │
                    ├─ Upload CC subtitles (Bilibili /x/v2/dm/subtitle/draft/save)
                    ├─ Post danmaku (Bilibili /x/v2/dm/post)
                    └─ subtitle_status → "completed"
```

Subtitle generation is **non-blocking** — it runs in a background goroutine after the video upload completes. The video is available on Bilibili immediately; CC subtitles and danmaku appear after human approval.

## Human Review Workflow

Unlike the old pipeline which auto-uploaded subtitles, the current flow requires explicit human approval:

1. **Draft generated** — whisper transcription + annotation stored in `subtitle_draft` column
2. **Preview** — frontend shows English SRT, Chinese SRT, and generated danmaku
3. **Optional re-annotation** — if danmaku quality is poor, regenerate via `/upload/annotate`
4. **Approve** — publishes CC subtitles + posts danmaku to Bilibili

## Annotation (Danmaku) Generation

Go calls Python as subprocess:

```
python -m app.annotate_cli --backend ollama
  stdin  → {"srt_content": "...", "video_title": "..."}
  stdout → {"annotations": [{"time": 10.5, "comment": "..."}, ...]}
```

The `AnnotationSkill` in Python generates persona-flavored comments (傲娇AI style). The skill prompt evolves through the reflection/approval flow in the AI进化 tab.

## Database

The `upload_jobs` table in `metadata.db` (Go-owned) tracks subtitle state:

| Column | Type | Description |
|--------|------|-------------|
| `subtitle_status` | TEXT | `pending` → `generating` → `review` → `completed` / `failed` |
| `subtitle_draft` | TEXT | JSON: `{english_srt, chinese_srt, annotations}` |
| `download_files` | TEXT | JSON array of downloaded file paths |

## HTTP Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/upload/subtitle-preview?id=N` | View subtitle draft (SRT + annotations) |
| `POST` | `/upload/annotate?id=N` | Regenerate annotations only (keeps SRT) |
| `POST` | `/upload/subtitle-approve?id=N` | Publish CC subtitles + post danmaku to Bilibili |
| `POST` | `/upload/retry-subtitle?id=N` | Re-run entire subtitle pipeline (transcribe + translate + annotate) |

## Configuration

CLI flags on the Go service:

| Flag | Default | Description |
|------|---------|-------------|
| `--enable-subtitles` | false | Enable subtitle pipeline |
| `--subtitle-binary` | (auto-detect) | Path to whisper_autosrt binary |
| `--subtitle-src-lang` | en | Source language |
| `--subtitle-dst-lang` | zh | Target language |
| `--subtitle-model` | base | Whisper model size |
| `--subtitle-embed-src` | false | Embed source language subtitles |
| `--subtitle-embed-dst` | false | Embed target language subtitles |
| `--ml-service-dir` | (required) | Path to Python ml-service (for annotate_cli) |
| `--llm-backend` | ollama | LLM backend for annotation |

## Dependencies

- **whisper_autosrt** — subtitle generation binary
- **Python 3** with ml-service venv — for `app.annotate_cli`
- **ffmpeg** — audio extraction
- **Internet access** — Google Translate API, Bilibili subtitle/danmaku API
- **Bilibili cookies** — required for CC upload and danmaku posting

## Known Limitations

- Translation uses Google Translate (free tier). May be rate-limited under heavy use.
- Whisper `base` model is fast but may miss words. Use `medium` or `large-v3` for accuracy.
- Danmaku posting has 500ms delay per comment to avoid Bilibili rate limiting.
- Subtitle generation timeout is 30 minutes per video.
- Whisper may crash on non-English source videos on Windows (`exit status 0xc0000409`).
