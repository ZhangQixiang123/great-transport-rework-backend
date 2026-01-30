package app

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

type Downloader interface {
	ListChannelVideoIDs(ctx context.Context, channelURL string, limit int, jsRuntime string) ([]string, error)
	DownloadVideo(ctx context.Context, videoURL, outputDir string, jsRuntime, format string) ([]string, error)
	GetVideoMetadata(ctx context.Context, videoID string, jsRuntime string) (*VideoMetadata, error)
	GetChannelVideosMetadata(ctx context.Context, channelURL string, limit int, jsRuntime string) ([]VideoMetadata, error)
}

// VideoMetadata contains full metadata for a YouTube video.
type VideoMetadata struct {
	ID           string   `json:"id"`
	Title        string   `json:"title"`
	Description  string   `json:"description"`
	Duration     int      `json:"duration"`
	ViewCount    int      `json:"view_count"`
	LikeCount    int      `json:"like_count"`
	CommentCount int      `json:"comment_count"`
	UploadDate   string   `json:"upload_date"`
	Thumbnail    string   `json:"thumbnail"`
	Tags         []string `json:"tags"`
	Categories   []string `json:"categories"`
	ChannelID    string   `json:"channel_id"`
	ChannelTitle string   `json:"channel"`
}

type YtDlpDownloader struct {
	sleep time.Duration
}

func NewYtDlpDownloader(sleep time.Duration) *YtDlpDownloader {
	return &YtDlpDownloader{sleep: sleep}
}

func (d *YtDlpDownloader) ListChannelVideoIDs(ctx context.Context, channelURL string, limit int, jsRuntime string) ([]string, error) {
	if limit <= 0 {
		return nil, fmt.Errorf("limit must be > 0")
	}
	args := []string{
		"--quiet",
		"--no-warnings",
		"--flat-playlist",
		"--print", "id",
		"--playlist-items", fmt.Sprintf("1:%d", limit),
		"--remote-components", "ejs:github",
		channelURL,
	}
	if jsRuntime != "" {
		args = append(args[:len(args)-1], "--js-runtimes", jsRuntime, channelURL)
	}
	lines, err := runYtDlpLines(ctx, args)
	if err != nil {
		return nil, err
	}
	ids := make([]string, 0, len(lines))
	for _, line := range lines {
		if line != "" {
			ids = append(ids, line)
		}
	}
	return ids, nil
}

// TODO: can return NA as path
func (d *YtDlpDownloader) DownloadVideo(ctx context.Context, videoURL, outputDir string, jsRuntime, format string) ([]string, error) {
	outputTemplate := filepath.Join(outputDir, "%(title)s.%(ext)s")
	baseArgs := []string{
		"--quiet",
		"--no-warnings",
		"--no-simulate",
		"--remote-components", "ejs:github",
		"-o", outputTemplate,
	}
	if HasExecutable("ffmpeg") {
		baseArgs = append(baseArgs, "--print", "after_postprocess:filepath")
	} else {
		baseArgs = append(baseArgs, "--print", "after_move:filepath")
	}
	if jsRuntime != "" {
		baseArgs = append(baseArgs, "--js-runtimes", jsRuntime)
	}
	if format != "" {
		baseArgs = append(baseArgs, "--format", format)
	}
	if d.sleep > 0 {
		baseArgs = append(baseArgs,
			fmt.Sprintf("--sleep-interval=%d", int(d.sleep.Seconds())),
			fmt.Sprintf("--max-sleep-interval=%d", int(d.sleep.Seconds())+1),
		)
	}

	runWithExtras := func(extra []string) (ytDlpResult, error) {
		args := make([]string, 0, len(baseArgs)+len(extra)+1)
		args = append(args, baseArgs...)
		args = append(args, extra...)
		args = append(args, videoURL)
		return runYtDlp(ctx, args)
	}

	res, err := runWithExtras(nil)
	if shouldRetryWithDynamic(res.stderr, err) {
		log.Println("yt-dlp indicated SABR fallback; retrying with --allow-dynamic-mpd --concurrent-fragments 1")
		res, err = runWithExtras([]string{"--allow-dynamic-mpd", "--concurrent-fragments", "1"})
	}
	if err != nil {
		return filterDownloadedFiles(res.files), fmt.Errorf("yt-dlp failed: %w", err)
	}

	files := filterDownloadedFiles(res.files)
	if len(files) == 0 {
		existing, lookupErr := resolveExistingFiles(ctx, videoURL, outputTemplate, jsRuntime, format)
		if lookupErr == nil && len(existing) > 0 {
			return existing, nil
		}
	}
	return files, nil
}

func runYtDlpLines(ctx context.Context, args []string) ([]string, error) {
	cmd := exec.CommandContext(ctx, "yt-dlp", args...)
	output, err := cmd.CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("yt-dlp failed: %w", err)
	}
	lines := []string{}
	for _, line := range strings.Split(string(output), "\n") {
		line = strings.TrimSpace(line)
		if line != "" {
			lines = append(lines, line)
		}
	}
	return lines, nil
}

type ytDlpResult struct {
	files  []string
	stderr string
}

func runYtDlp(ctx context.Context, args []string) (ytDlpResult, error) {
	cmd := exec.CommandContext(ctx, "yt-dlp", args...)
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return ytDlpResult{}, err
	}
	var stderrBuf bytes.Buffer
	cmd.Stderr = io.MultiWriter(os.Stderr, &stderrBuf)

	if err := cmd.Start(); err != nil {
		return ytDlpResult{}, err
	}

	var files []string
	scanner := bufio.NewScanner(stdout)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line != "" {
			files = append(files, line)
		}
	}
	if scanErr := scanner.Err(); scanErr != nil {
		return ytDlpResult{files: files, stderr: stderrBuf.String()}, scanErr
	}
	if err := cmd.Wait(); err != nil {
		return ytDlpResult{files: files, stderr: stderrBuf.String()}, err
	}
	return ytDlpResult{files: files, stderr: stderrBuf.String()}, nil
}

func resolveExistingFiles(ctx context.Context, videoURL, outputTemplate, jsRuntime, format string) ([]string, error) {
	args := []string{
		"--quiet",
		"--no-warnings",
		"--no-download",
		"--print", "filename",
		"--remote-components", "ejs:github",
		"-o", outputTemplate,
	}
	if jsRuntime != "" {
		args = append(args, "--js-runtimes", jsRuntime)
	}
	if format != "" {
		args = append(args, "--format", format)
	}
	args = append(args, videoURL)

	lines, err := runYtDlpLines(ctx, args)
	if err != nil {
		return nil, err
	}
	files := make([]string, 0, len(lines))
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" || line == "NA" {
			continue
		}
		if _, statErr := os.Stat(line); statErr == nil {
			files = append(files, line)
		}
	}
	return files, nil
}

func filterDownloadedFiles(files []string) []string {
	result := make([]string, 0, len(files))
	for _, file := range files {
		file = strings.TrimSpace(file)
		if file == "" || file == "NA" {
			continue
		}
		result = append(result, file)
	}
	return result
}

func shouldRetryWithDynamic(stderr string, runErr error) bool {
	if stderr == "" && runErr == nil {
		return false
	}
	patterns := []string{
		"fragment not found",
		"Retrying fragment",
		"SABR streaming",
		"Some web client https formats have been skipped",
		"HTTP Error 403",
	}
	for _, p := range patterns {
		if strings.Contains(stderr, p) {
			return true
		}
	}
	return false
}

// GetVideoMetadata retrieves full metadata for a single video.
func (d *YtDlpDownloader) GetVideoMetadata(ctx context.Context, videoID string, jsRuntime string) (*VideoMetadata, error) {
	videoURL := fmt.Sprintf("https://www.youtube.com/watch?v=%s", videoID)
	args := []string{
		"--quiet",
		"--no-warnings",
		"--dump-json",
		"--skip-download",
		"--remote-components", "ejs:github",
	}
	if jsRuntime != "" {
		args = append(args, "--js-runtimes", jsRuntime)
	}
	args = append(args, videoURL)

	output, err := runYtDlpOutput(ctx, args)
	if err != nil {
		return nil, err
	}

	var meta VideoMetadata
	if err := json.Unmarshal(output, &meta); err != nil {
		return nil, fmt.Errorf("parse metadata: %w", err)
	}
	return &meta, nil
}

// GetChannelVideosMetadata retrieves metadata for videos from a channel.
func (d *YtDlpDownloader) GetChannelVideosMetadata(ctx context.Context, channelURL string, limit int, jsRuntime string) ([]VideoMetadata, error) {
	if limit <= 0 {
		return nil, fmt.Errorf("limit must be > 0")
	}
	args := []string{
		"--quiet",
		"--no-warnings",
		"--dump-json",
		"--skip-download",
		"--playlist-items", fmt.Sprintf("1:%d", limit),
		"--remote-components", "ejs:github",
	}
	if jsRuntime != "" {
		args = append(args, "--js-runtimes", jsRuntime)
	}
	args = append(args, channelURL)

	output, err := runYtDlpOutput(ctx, args)
	if err != nil {
		return nil, err
	}

	// yt-dlp outputs one JSON object per line (JSONL format)
	var videos []VideoMetadata
	for _, line := range bytes.Split(output, []byte("\n")) {
		line = bytes.TrimSpace(line)
		if len(line) == 0 {
			continue
		}
		var meta VideoMetadata
		if err := json.Unmarshal(line, &meta); err != nil {
			log.Printf("warning: failed to parse video metadata line: %v", err)
			continue
		}
		videos = append(videos, meta)
	}
	return videos, nil
}

func runYtDlpOutput(ctx context.Context, args []string) ([]byte, error) {
	cmd := exec.CommandContext(ctx, "yt-dlp", args...)
	output, err := cmd.Output()
	if err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			return nil, fmt.Errorf("yt-dlp failed: %w: %s", err, string(exitErr.Stderr))
		}
		return nil, fmt.Errorf("yt-dlp failed: %w", err)
	}
	return output, nil
}
