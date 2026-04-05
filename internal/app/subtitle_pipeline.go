package app

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"
)

// SubtitlePipelineConfig holds settings for the subtitle generation pipeline.
type SubtitlePipelineConfig struct {
	PythonBinary    string // path to python3 binary (default: "python3")
	WhisperScript   string // path to whisper_transcribe.py
	WhisperModel    string // whisper model size (default: "base")
	CookiePath      string // path to biliup cookies.json
	AnnotationURL   string // deprecated: kept for backward compat but ignored
	LLMBackend      string // LLM backend for annotations: ollama, openai, anthropic (default: "ollama")
	MLServiceDir    string // path to ml-service directory (for running annotate_cli)
}

// SubtitleDraft holds generated subtitle + annotation data awaiting review.
type SubtitleDraft struct {
	EnglishSRT  string     `json:"english_srt"`
	ChineseSRT  string     `json:"chinese_srt"`
	Annotations []bccEntry `json:"annotations"`
}

// RunSubtitlePipeline generates subtitles and annotations, saves as draft for review.
// Does NOT upload — use ApproveSubtitle to publish after review.
func RunSubtitlePipeline(ctx context.Context, cfg SubtitlePipelineConfig, store *SQLiteStore, jobID int64, videoPath, bvid string) error {
	// Step 1: Transcribe
	slog.Info("subtitle-pipeline: transcribing", "file", filepath.Base(videoPath))
	englishSRT, err := whisperTranscribe(ctx, cfg, videoPath)
	if err != nil {
		return fmt.Errorf("transcription failed: %w", err)
	}
	if englishSRT == "" {
		return fmt.Errorf("transcription produced empty result")
	}
	slog.Info("subtitle-pipeline: transcription complete, translating to Chinese")

	// Step 2: Translate
	chineseSRT, err := translateSRT(englishSRT)
	if err != nil {
		return fmt.Errorf("translation failed: %w", err)
	}
	if chineseSRT == "" {
		return fmt.Errorf("translation produced empty result")
	}
	slog.Info("subtitle-pipeline: translation complete")

	// Step 3: Generate persona annotations (optional)
	var annotations []bccEntry
	if cfg.MLServiceDir != "" {
		videoTitle := strings.TrimSuffix(filepath.Base(videoPath), filepath.Ext(videoPath))
		entries, err := fetchAnnotations(cfg, chineseSRT, videoTitle)
		if err != nil {
			slog.Warn("subtitle-pipeline: annotation failed (non-fatal)", "error", err)
		} else {
			annotations = entries
			slog.Info("subtitle-pipeline: generated annotations", "count", len(entries))
		}
	}

	// Save draft to DB for review instead of uploading
	draft := SubtitleDraft{
		EnglishSRT:  englishSRT,
		ChineseSRT:  chineseSRT,
		Annotations: annotations,
	}
	draftJSON, err := json.Marshal(draft)
	if err != nil {
		return fmt.Errorf("marshaling draft: %w", err)
	}
	if err := store.SaveSubtitleDraft(ctx, jobID, string(draftJSON)); err != nil {
		return fmt.Errorf("saving draft: %w", err)
	}

	_ = store.UpdateSubtitleStatus(ctx, jobID, "review")
	slog.Info("subtitle-pipeline: draft saved for review", "job_id", jobID, "bvid", bvid, "annotations", len(annotations))
	return nil
}

// ApproveSubtitle publishes a reviewed subtitle draft: uploads CC + posts danmaku.
func ApproveSubtitle(ctx context.Context, cfg SubtitlePipelineConfig, store *SQLiteStore, jobID int64, bvid string) error {
	draftJSON, err := store.GetSubtitleDraft(ctx, jobID)
	if err != nil {
		return fmt.Errorf("loading draft: %w", err)
	}
	var draft SubtitleDraft
	if err := json.Unmarshal([]byte(draftJSON), &draft); err != nil {
		return fmt.Errorf("parsing draft: %w", err)
	}

	// Upload bilingual CC
	if err := uploadBilingualSubtitle(bvid, draft.EnglishSRT, draft.ChineseSRT, cfg.CookiePath); err != nil {
		return fmt.Errorf("CC upload failed: %w", err)
	}
	slog.Info("subtitle-approve: uploaded bilingual CC", "bvid", bvid)

	// Post annotations as danmaku
	if len(draft.Annotations) > 0 {
		posted := postDanmakuBatch(bvid, draft.Annotations, cfg.CookiePath)
		slog.Info("subtitle-approve: posted danmaku", "posted", posted, "total", len(draft.Annotations), "bvid", bvid)
	}

	_ = store.UpdateSubtitleStatus(ctx, jobID, "completed")
	return nil
}

// annotateRequest is the JSON input for the Python annotate_cli script.
type annotateRequest struct {
	SRTContent     string `json:"srt_content"`
	VideoTitle     string `json:"video_title"`
	MaxAnnotations int    `json:"max_annotations"`
}

// annotateResponse is the JSON output from the Python annotate_cli script.
type annotateResponse struct {
	Annotations []bccEntry `json:"annotations"`
	Count       int        `json:"count"`
}

// fetchAnnotations calls the Python annotate_cli subprocess to generate persona comments.
func fetchAnnotations(cfg SubtitlePipelineConfig, chineseSRT, videoTitle string) ([]bccEntry, error) {
	python := cfg.PythonBinary
	if python == "" {
		if runtime.GOOS == "windows" {
			python = "python"
		} else {
			python = "python3"
		}
	}

	backend := cfg.LLMBackend
	if backend == "" {
		backend = "ollama"
	}

	reqBody, err := json.Marshal(annotateRequest{
		SRTContent:     chineseSRT,
		VideoTitle:     videoTitle,
		MaxAnnotations: 0,
	})
	if err != nil {
		return nil, fmt.Errorf("marshaling request: %w", err)
	}

	// Use the venv python relative to MLServiceDir (which is cmd.Dir)
	pythonPath := filepath.Join(".venv", "Scripts", "python")
	if runtime.GOOS != "windows" {
		pythonPath = filepath.Join(".venv", "bin", "python3")
	}

	args := []string{"-m", "app.annotate_cli", "--backend", backend}
	cmd := exec.Command(pythonPath, args...)
	cmd.Stdin = bytes.NewReader(reqBody)
	cmd.Dir = cfg.MLServiceDir
	cmd.Env = append(cmd.Environ(), "PYTHONUTF8=1")

	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	slog.Info("annotate: running CLI", "backend", backend)
	if err := cmd.Run(); err != nil {
		slog.Error("annotate: CLI failed", "stderr", stderr.String())
		return nil, fmt.Errorf("annotate_cli failed: %w", err)
	}

	if stderr.Len() > 0 {
		slog.Debug("annotate: CLI logs", "stderr", stderr.String())
	}

	var result annotateResponse
	if err := json.Unmarshal(stdout.Bytes(), &result); err != nil {
		return nil, fmt.Errorf("parsing annotation output: %w (raw: %s)", err, stdout.String()[:min(200, stdout.Len())])
	}

	return result.Annotations, nil
}

// uploadBilingualSubtitle uploads bilingual (EN+ZH) CC subtitles to Bilibili.
// Each BCC entry contains "中文翻译\nEnglish original".
func uploadBilingualSubtitle(bvid, englishSRT, chineseSRT, cookiePath string) error {
	creds, err := loadBilibiliCookies(cookiePath)
	if err != nil {
		return fmt.Errorf("loading cookies: %w", err)
	}

	bcc := bilingualSRTToBCC(englishSRT, chineseSRT)
	if len(bcc.Body) == 0 {
		return fmt.Errorf("SRT has no usable subtitle entries")
	}

	cid, err := getCID(bvid, creds.SESSDATA)
	if err != nil {
		return fmt.Errorf("getting CID: %w", err)
	}

	bccJSON, err := json.Marshal(bcc)
	if err != nil {
		return fmt.Errorf("marshaling BCC: %w", err)
	}

	form := url.Values{
		"type":   {"1"},
		"oid":    {strconv.FormatInt(cid, 10)},
		"lan":    {"zh"},
		"bvid":   {bvid},
		"submit": {"true"},
		"sign":   {"false"},
		"csrf":   {creds.BiliJct},
		"data":   {string(bccJSON)},
	}

	req, err := http.NewRequest("POST", bilibiliAPI+"/x/v2/dm/subtitle/draft/save",
		strings.NewReader(form.Encode()))
	if err != nil {
		return fmt.Errorf("creating request: %w", err)
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("Referer", "https://www.bilibili.com")
	req.AddCookie(&http.Cookie{Name: "SESSDATA", Value: creds.SESSDATA})

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return fmt.Errorf("uploading subtitle: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("reading upload response: %w", err)
	}

	var result struct {
		Code    int    `json:"code"`
		Message string `json:"message"`
	}
	if err := json.Unmarshal(respBody, &result); err != nil {
		return fmt.Errorf("parsing upload response: %w", err)
	}
	if result.Code != 0 {
		return fmt.Errorf("Bilibili subtitle API error: %s (code=%d)", result.Message, result.Code)
	}

	slog.Info("subtitle: uploaded bilingual CC", "bvid", bvid, "cid", cid, "entries", len(bcc.Body))
	return nil
}

// postDanmakuBatch posts annotation entries as danmaku. Returns count of successfully posted.
func postDanmakuBatch(bvid string, entries []bccEntry, cookiePath string) int {
	creds, err := loadBilibiliCookies(cookiePath)
	if err != nil {
		slog.Error("danmaku: failed to load cookies", "error", err)
		return 0
	}

	cid, err := getCID(bvid, creds.SESSDATA)
	if err != nil {
		slog.Error("danmaku: failed to get CID", "bvid", bvid, "error", err)
		return 0
	}

	posted := 0
	for _, e := range entries {
		if err := postDanmaku(cid, bvid, e, creds); err != nil {
			slog.Warn("danmaku: failed to post", "timestamp", e.From, "error", err)
			continue
		}
		posted++
		// Small delay to avoid rate limiting
		time.Sleep(500 * time.Millisecond)
	}
	return posted
}

// postDanmaku posts a single danmaku comment to Bilibili.
func postDanmaku(cid int64, bvid string, entry bccEntry, creds *bilibiliCreds) error {
	progressMs := int64(entry.From * 1000)

	form := url.Values{
		"type":       {"1"},                               // 1 = video danmaku
		"oid":        {strconv.FormatInt(cid, 10)},        // cid
		"msg":        {entry.Content},                     // danmaku text
		"bvid":       {bvid},                              // video bvid
		"progress":   {strconv.FormatInt(progressMs, 10)}, // timestamp in ms
		"color":      {"16738740"},                         // #FF6B34 orange-red
		"fontsize":   {"25"},                               // normal size
		"pool":       {"0"},                                // normal pool
		"mode":       {"5"},                                // 5 = top fixed
		"plat":       {"1"},                                // 1 = web
		"csrf":       {creds.BiliJct},
		"csrf_token": {creds.BiliJct},
	}

	req, err := http.NewRequest("POST", bilibiliAPI+"/x/v2/dm/post",
		strings.NewReader(form.Encode()))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("Referer", "https://www.bilibili.com/video/"+bvid)
	req.Header.Set("Origin", "https://www.bilibili.com")
	req.Header.Set("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
	req.AddCookie(&http.Cookie{Name: "SESSDATA", Value: creds.SESSDATA})
	req.AddCookie(&http.Cookie{Name: "bili_jct", Value: creds.BiliJct})

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return fmt.Errorf("posting danmaku: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("reading danmaku response: %w", err)
	}

	var result struct {
		Code    int    `json:"code"`
		Message string `json:"message"`
	}
	if err := json.Unmarshal(body, &result); err != nil {
		return fmt.Errorf("parsing danmaku response: %s", string(body))
	}
	if result.Code != 0 {
		return fmt.Errorf("danmaku API error: %s (code=%d)", result.Message, result.Code)
	}

	return nil
}

// whisperTranscribe calls the whisper_transcribe.py script and returns SRT content.
func whisperTranscribe(ctx context.Context, cfg SubtitlePipelineConfig, videoPath string) (string, error) {
	python := cfg.PythonBinary
	if python == "" {
		if runtime.GOOS == "windows" {
			python = "python"
		} else {
			python = "python3"
		}
	}

	script := cfg.WhisperScript
	if script == "" {
		// Default: look for script relative to the binary
		script = "scripts/whisper_transcribe.py"
	}

	model := cfg.WhisperModel
	if model == "" {
		model = "base"
	}

	args := []string{script, videoPath, "--model", model}
	cmd := exec.CommandContext(ctx, python, args...)

	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	slog.Debug("subtitle-pipeline: running whisper", "python", python, "args", args)
	if err := cmd.Run(); err != nil {
		slog.Error("subtitle-pipeline: whisper failed", "stderr", stderr.String())
		return "", fmt.Errorf("whisper process failed: %w", err)
	}

	if stderr.Len() > 0 {
		slog.Debug("subtitle-pipeline: whisper output", "stderr", stderr.String())
	}

	return stdout.String(), nil
}
