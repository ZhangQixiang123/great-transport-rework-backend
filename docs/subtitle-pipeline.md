# Subtitle Pipeline

Automatic Chinese CC subtitle generation for uploaded Bilibili videos.

## Architecture

The subtitle pipeline runs entirely within the Go service. Python is only used
as a one-shot subprocess for Whisper transcription (no Python service needed).

```
Go JobQueue (after video upload completes):
  1. Transcribe audio → English SRT    (python3 whisper_transcribe.py, subprocess)
  2. Translate → Chinese SRT           (Google Translate HTTP API, in Go)
  3. Convert SRT → Bilibili BCC JSON   (in Go)
  4. Upload CC to Bilibili              (Bilibili subtitle API, in Go)
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
      ├─ whisper_transcribe.py <video_file> → English SRT (stdout)
      ├─ Google Translate each line → Chinese SRT
      ├─ SRT → BCC conversion → Bilibili /x/v2/dm/subtitle/draft/save
      └─ subtitle_status → "completed" or "failed"
```

Subtitle generation is **non-blocking** — it runs in a background goroutine
after the video upload completes. The video is available on Bilibili immediately;
CC subtitles appear once the pipeline finishes.

## Database

The `upload_jobs` table tracks subtitle state:

| Column | Type | Description |
|--------|------|-------------|
| `download_files` | TEXT | JSON array of downloaded file paths |
| `subtitle_status` | TEXT | `pending` → `generating` → `completed` / `failed` |

## HTTP Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/upload/needs-subtitles` | List completed jobs with `subtitle_status = 'pending'` |
| `POST` | `/upload/subtitle` | Manually submit SRT content: `{job_id, srt_content}` — Go converts to BCC and uploads to Bilibili |
| `GET` | `/upload/status?id=N` | Includes `subtitle_status` and `download_files` in response |

## Configuration

The subtitle pipeline is **auto-configured** when a `BiliupUploader` with a
cookie path is set on the controller. No extra flags needed.

Defaults:
- Whisper model: `base`
- Whisper script: `scripts/whisper_transcribe.py`
- Cookie path: inherited from `BiliupUploader.CookiePath`
- Python binary: `python3` (or `python` on Windows)

To override, set `queue.subtitleCfg` before starting the queue:

```go
queue.subtitleCfg = &app.SubtitlePipelineConfig{
    PythonBinary:  "/path/to/python3",
    WhisperScript: "scripts/whisper_transcribe.py",
    WhisperModel:  "medium",   // better accuracy, slower
    CookiePath:    "cookies.json",
}
```

## Dependencies

On the machine running the Go service:

- **Python 3** with `faster-whisper` installed: `pip install faster-whisper`
- **ffmpeg** (used by faster-whisper for audio extraction)
- **Internet access** for Google Translate API and Bilibili subtitle API

## Known Limitations

- Translation uses Google Translate (free tier, `translate.google.com/m`).
  May be rate-limited under heavy use.
- Each subtitle line is translated individually (no batch API).
  A 400-segment video takes ~6 minutes to translate.
- Whisper `base` model is fast but may miss some words. Use `medium` or
  `large-v3` for better accuracy at the cost of speed.
- Subtitle generation timeout is 30 minutes per video.
