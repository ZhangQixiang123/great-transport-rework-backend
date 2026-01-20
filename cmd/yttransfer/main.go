package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"os"
	"os/exec"
	"strings"
	"sync"
	"time"

	"great_transport/internal/app"
)

var (
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
	biliupBinary string
	biliupCookie string
	biliupLine   string
	biliupLimit  int
	biliupTags   string
	biliupTitle  string
	biliupDesc   string
	biliupDynamic string
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

	if _, err := app.LookPath("yt-dlp"); err != nil {
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
	store, err := app.NewSQLiteStore(cfg.dbPath)
	if err != nil {
		log.Fatal(err)
	}
	if err := store.EnsureSchema(ctx); err != nil {
		log.Fatal(err)
	}

	downloader := app.NewYtDlpDownloader(time.Duration(cfg.sleepSeconds) * time.Second)
	uploader, err := newUploaderFromConfig(cfg)
	if err != nil {
		log.Fatal(err)
	}
	controller := &app.Controller{
		Downloader: downloader,
		Uploader:   uploader,
		Store:      store,
		OutputDir:  cfg.outputDir,
		JSRuntime:  jsRuntime,
		Format:     format,
	}

	if cfg.httpAddr != "" {
		if err := app.ServeHTTP(cfg.httpAddr, controller); err != nil {
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

func newUploaderFromConfig(cfg config) (app.Uploader, error) {
	switch cfg.platform {
	case "bilibili":
		opts := app.BiliupUploaderOptions{
			Binary:      cfg.biliupBinary,
			CookiePath:  cfg.biliupCookie,
			Line:        cfg.biliupLine,
			Limit:       cfg.biliupLimit,
			TitlePrefix: cfg.biliupTitle,
			Description: cfg.biliupDesc,
			Dynamic:     cfg.biliupDynamic,
			Tags:        parseCSVList(cfg.biliupTags),
		}
		return app.NewBiliupUploader(opts), nil
	case "tiktok":
		return dummyUploader{platform: cfg.platform}, nil
	default:
		return nil, fmt.Errorf("unsupported platform: %s", cfg.platform)
	}
}

func parseCSVList(input string) []string {
	parts := strings.Split(input, ",")
	result := make([]string, 0, len(parts))
	for _, part := range parts {
		part = strings.TrimSpace(part)
		if part != "" {
			result = append(result, part)
		}
	}
	return result
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
	fs.StringVar(&cfg.format, "format", "auto", "yt-dlp format selector (auto prefers mp4 when available)")
	fs.StringVar(&cfg.biliupBinary, "biliup-binary", "biliup", "path to biliup CLI binary")
	fs.StringVar(&cfg.biliupCookie, "biliup-cookie", "cookies.json", "path to biliup cookies.json (created after `biliup login`)")
	fs.StringVar(&cfg.biliupLine, "biliup-line", "", "optional biliup upload line override (ws/qn/bda2/...)")
	fs.IntVar(&cfg.biliupLimit, "biliup-limit", 3, "per-file biliup upload concurrency limit")
	fs.StringVar(&cfg.biliupTags, "biliup-tags", "", "comma-separated biliup tags")
	fs.StringVar(&cfg.biliupTitle, "biliup-title-prefix", "", "prefix prepended to derived biliup video titles")
	fs.StringVar(&cfg.biliupDesc, "biliup-desc", "Uploaded via yt-transfer", "description text template for biliup uploads")
	fs.StringVar(&cfg.biliupDynamic, "biliup-dynamic", "", "dynamic/status text for biliup uploads (defaults to description)")
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
		if app.HasExecutable(candidate) {
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
		if strings.Contains(value, "+") && !app.HasExecutable("ffmpeg") {
			return value, "ffmpeg not found; yt-dlp may fail to merge formats requested via --format"
		}
		return value, ""
	}
	if app.HasExecutable("ffmpeg") {
		return "bv*[ext=mp4]+ba[ext=m4a]/bv*[ext=mp4]/b[ext=mp4]/bv*+ba/b", ""
	}
	return "b[ext=mp4]/b", "ffmpeg not found; falling back to single-stream downloads. Install ffmpeg for merged video+audio output."
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
