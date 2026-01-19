package main

import (
	"context"
	"fmt"
	"log"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

type Downloader interface {
	ListChannelVideoIDs(ctx context.Context, channelURL string, limit int, jsRuntime string) ([]string, error)
	DownloadVideo(ctx context.Context, videoURL, outputDir string, jsRuntime, format string) ([]string, error)
}

type ytDlpDownloader struct {
	sleep time.Duration
}

func (d *ytDlpDownloader) ListChannelVideoIDs(ctx context.Context, channelURL string, limit int, jsRuntime string) ([]string, error) {
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

func (d *ytDlpDownloader) DownloadVideo(ctx context.Context, videoURL, outputDir string, jsRuntime, format string) ([]string, error) {
	outputTemplate := filepath.Join(outputDir, "%(title)s.%(ext)s")
	baseArgs := []string{
		"--quiet",
		"--no-warnings",
		"--no-simulate",
		"--remote-components", "ejs:github",
		"--print", "after_move:filepath",
		"-o", outputTemplate,
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
		return res.files, fmt.Errorf("yt-dlp failed: %w", err)
	}

	return res.files, nil
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
