package app

import (
	"bufio"
	"bytes"
	"context"
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
	DownloadVideo(ctx context.Context, videoURL, outputDir string, jsRuntime, format string) ([]string, error)
}

type YtDlpDownloader struct {
	sleep time.Duration
}

func NewYtDlpDownloader(sleep time.Duration) *YtDlpDownloader {
	return &YtDlpDownloader{sleep: sleep}
}

func (d *YtDlpDownloader) DownloadVideo(ctx context.Context, videoURL, outputDir string, jsRuntime, format string) ([]string, error) {
	outputTemplate := filepath.Join(outputDir, "%(title)s.%(ext)s")
	baseArgs := []string{
		"--quiet",
		"--no-warnings",
		"--no-simulate",
		"--no-check-certificates",
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

type ytDlpResult struct {
	files  []string
	stderr string
}

// ytDlpEnv returns the current environment with PYTHONIOENCODING=utf-8
// to prevent mojibake in yt-dlp output on Windows (GBK console).
func ytDlpEnv() []string {
	env := os.Environ()
	env = append(env, "PYTHONIOENCODING=utf-8")
	return env
}

func runYtDlp(ctx context.Context, args []string) (ytDlpResult, error) {
	cmd := exec.CommandContext(ctx, "yt-dlp", args...)
	cmd.Env = ytDlpEnv()
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

func runYtDlpLines(ctx context.Context, args []string) ([]string, error) {
	cmd := exec.CommandContext(ctx, "yt-dlp", args...)
	cmd.Env = ytDlpEnv()
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

func resolveExistingFiles(ctx context.Context, videoURL, outputTemplate, jsRuntime, format string) ([]string, error) {
	args := []string{
		"--quiet",
		"--no-warnings",
		"--no-check-certificates",
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
