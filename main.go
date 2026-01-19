package main

import (
	"bufio"
	"bytes"
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"os/exec"
	"strings"
	"sync"
	"time"
)

var (
	lookPath        = exec.LookPath
	ytDlpHelpRun    = func() ([]byte, error) { return exec.Command("yt-dlp", "--help").CombinedOutput() }
	jsFlagOnce      sync.Once
	jsFlagSupported bool
	jsFlagErr       error
)

type config struct {
	channelID    string
	videoID      string
	platform     string
	outputDir    string
	dbPath       string
	httpAddr     string
	limit        int
	sleepSeconds int
	jsRuntime    string
	format       string
}

type uploader interface {
	Upload(path string) error
}

type dummyUploader struct {
	platform string
}

func (u dummyUploader) Upload(path string) error {
	log.Printf("stub upload to %s: %s", u.platform, path)
	return nil
}

func main() {
	log.SetFlags(0)

	cfg, err := parseFlags()
	if err != nil {
		log.Fatal(err)
	}

	if _, err := lookPath("yt-dlp"); err != nil {
		log.Fatal("yt-dlp not found in PATH; install it first (see README for Docker setup)")
	}

	if err := os.MkdirAll(cfg.outputDir, 0o755); err != nil {
		log.Fatal(err)
	}

	jsRuntime, jsWarn, err := resolveDesiredJSRuntime(cfg.jsRuntime)
	if err != nil {
		log.Fatal(err)
	}
	if jsWarn != "" {
		log.Println(jsWarn)
	}
	format, warn := determineFormat(cfg.format)
	if warn != "" {
		log.Println(warn)
	}

	ctx := context.Background()
	store, err := NewSQLiteStore(cfg.dbPath)
	if err != nil {
		log.Fatal(err)
	}
	if err := store.EnsureSchema(ctx); err != nil {
		log.Fatal(err)
	}

	downloader := &ytDlpDownloader{
		sleep: time.Duration(cfg.sleepSeconds) * time.Second,
	}
	up := dummyUploader{platform: cfg.platform}
	controller := &Controller{
		Downloader: downloader,
		Uploader:   up,
		Store:      store,
		OutputDir:  cfg.outputDir,
		JSRuntime:  jsRuntime,
		Format:     format,
	}

	if cfg.httpAddr != "" {
		if err := serveHTTP(cfg.httpAddr, controller); err != nil {
			log.Fatal(err)
		}
		return
	}

	switch {
	case cfg.channelID != "":
		if _, err := controller.SyncChannel(ctx, cfg.channelID, cfg.limit); err != nil {
			log.Fatal(err)
		}
	case cfg.videoID != "":
		if err := controller.SyncVideo(ctx, cfg.videoID); err != nil {
			log.Fatal(err)
		}
	default:
		log.Fatal("no channel or video provided; use --http-addr for server mode")
	}
}

func parseFlags() (config, error) {
	return parseFlagsFrom(flag.CommandLine, os.Args[1:])
}

func parseFlagsFrom(fs *flag.FlagSet, args []string) (config, error) {
	var cfg config
	fs.StringVar(&cfg.channelID, "channel-id", "", "YouTube channel ID or URL")
	fs.StringVar(&cfg.videoID, "video-id", "", "YouTube video ID or URL")
	fs.StringVar(&cfg.platform, "platform", "bilibili", "target platform (bilibili or tiktok)")
	fs.StringVar(&cfg.outputDir, "output", "downloads", "output directory")
	fs.StringVar(&cfg.dbPath, "db-path", "metadata.db", "path to sqlite metadata database")
	fs.StringVar(&cfg.httpAddr, "http-addr", "", "HTTP listen address (enables controller server mode)")
	fs.IntVar(&cfg.limit, "limit", 5, "max videos to download for channel")
	fs.IntVar(&cfg.sleepSeconds, "sleep-seconds", 5, "sleep seconds between downloads")
	fs.StringVar(&cfg.jsRuntime, "js-runtime", "auto", "JS runtime passed to yt-dlp (auto,node,deno,...)")
	fs.StringVar(&cfg.format, "format", "auto", "yt-dlp format selector (auto picks best available for the environment)")
	if err := fs.Parse(args); err != nil {
		return cfg, err
	}

	if cfg.httpAddr == "" && cfg.channelID == "" && cfg.videoID == "" {
		return cfg, errors.New("provide either --channel-id or --video-id")
	}
	if cfg.httpAddr == "" && cfg.channelID != "" && cfg.videoID != "" {
		return cfg, errors.New("provide only one of --channel-id or --video-id")
	}
	if cfg.channelID != "" && cfg.limit <= 0 {
		return cfg, errors.New("--limit must be > 0 for channel downloads")
	}
	if cfg.sleepSeconds < 0 {
		return cfg, errors.New("--sleep-seconds must be >= 0")
	}

	cfg.platform = strings.ToLower(strings.TrimSpace(cfg.platform))
	switch cfg.platform {
	case "bilibili", "tiktok":
	default:
		return cfg, errors.New("--platform must be bilibili or tiktok")
	}

	return cfg, nil
}

func resolveDesiredJSRuntime(pref string) (string, string, error) {
	supported, err := jsRuntimeFlagSupported()
	if err != nil {
		return "", "", err
	}
	if !supported {
		if runtimePrefIsAuto(pref) {
			return "", "yt-dlp in PATH does not support --js-runtimes; continuing without explicit JS runtime", nil
		}
		return "", "", errors.New("--js-runtime requires yt-dlp 2024.04.09 or newer; update yt-dlp or remove the flag")
	}
	runtime, err := resolveJSRuntime(pref)
	if err != nil {
		return "", "", err
	}
	return runtime, "", nil
}

func resolveJSRuntime(preferred string) (string, error) {
	candidates := []string{}
	for _, part := range strings.Split(strings.ToLower(strings.TrimSpace(preferred)), ",") {
		part = strings.TrimSpace(part)
		if part != "" && part != "auto" {
			candidates = append(candidates, part)
		}
	}
	if len(candidates) == 0 {
		candidates = []string{"node", "deno"}
	}
	for _, candidate := range candidates {
		if hasExecutable(candidate) {
			return candidate, nil
		}
	}
	return "", fmt.Errorf("no supported JS runtime found (tried %s)", strings.Join(candidates, ", "))
}

func runtimePrefIsAuto(value string) bool {
	v := strings.ToLower(strings.TrimSpace(value))
	return v == "" || v == "auto"
}

func determineFormat(selection string) (string, string) {
	value := strings.TrimSpace(selection)
	if value != "" && value != "auto" {
		if strings.Contains(value, "+") && !hasExecutable("ffmpeg") {
			return value, "ffmpeg not found; yt-dlp may fail to merge formats requested via --format"
		}
		return value, ""
	}
	if hasExecutable("ffmpeg") {
		return "bv*+ba/b", ""
	}
	return "", "ffmpeg not found; falling back to single-stream downloads. Install ffmpeg for merged video+audio output."
}

func hasExecutable(name string) bool {
	if name == "" {
		return false
	}
	_, err := lookPath(name)
	return err == nil
}

func jsRuntimeFlagSupported() (bool, error) {
	jsFlagOnce.Do(func() {
		out, err := ytDlpHelpRun()
		if err != nil {
			jsFlagErr = err
			return
		}
		jsFlagSupported = strings.Contains(string(out), "--js-runtimes")
	})
	return jsFlagSupported, jsFlagErr
}

func channelURL(input string) string {
	if looksLikeURL(input) {
		return input
	}
	return fmt.Sprintf("https://www.youtube.com/channel/%s/videos", input)
}

func videoURL(input string) string {
	if looksLikeURL(input) {
		return input
	}
	return fmt.Sprintf("https://www.youtube.com/watch?v=%s", input)
}

func looksLikeURL(input string) bool {
	return strings.HasPrefix(input, "http://") || strings.HasPrefix(input, "https://")
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
