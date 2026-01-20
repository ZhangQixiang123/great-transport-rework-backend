# YouTube Downloader + Upload Stub (Go)

Minimal CLI to download from a YouTube channel or single video ID, then pass results to a stub uploader for China platforms.

## Requirements
- `yt-dlp` in `PATH`
- `ffmpeg` recommended (improves format handling)
- [`biliup` CLI](https://github.com/biliup/biliup) in `PATH` (only required for Bilibili uploads)
  - Run `biliup login --user-cookie cookies.json` once to create upload credentials referenced by this tool.

## Usage
```bash
go run . --video-id dQw4w9WgXcQ --platform bilibili
go run . --channel-id UC_x5XG1OV2P6uZZ5FSM9Ttw --limit 3 --sleep-seconds 5
```

Options:
- `--channel-id` YouTube channel ID or URL
- `--video-id` YouTube video ID or URL
- `--platform` `bilibili` or `tiktok`
- `--output` output directory (default: `downloads`)
- `--limit` max videos for channel downloads (default: 5)
- `--sleep-seconds` sleep between downloads to reduce rate (default: 5)
- `--biliup-cookie`, `--biliup-line`, `--biliup-limit`, `--biliup-tags`, etc. expose uploader-level knobs; run `go run ./cmd/yttransfer --help` for details.

## Docker
```bash
docker build -t yt-transfer .
docker run --rm -v "$PWD/downloads:/app/downloads" yt-transfer \
  --channel-id UC_x5XG1OV2P6uZZ5FSM9Ttw --limit 3 --sleep-seconds 5
```
To authenticate biliup inside the container, mount a cookie file and run the login command once:
```bash
touch cookies.json  # create locally if it does not exist
docker run --rm -it \
  -v "$PWD/cookies.json:/app/cookies.json" \
  --entrypoint biliup yt-transfer \
  login --user-cookie /app/cookies.json
```
The uploader reads `/app/cookies.json` by default, so keep mounting that file for later runs.

## Notes
- Bilibili uploads execute the [`biliup`](https://github.com/biliup/biliup) CLI; install it and log in before running.
- TikTok uploads are still stubbed and only log the file paths.
- For channel downloads, the tool limits to the newest `--limit` videos.
