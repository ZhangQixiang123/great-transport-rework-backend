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

	// Channel management
	addChannel    string
	removeChannel string
	listChannels  bool

	// Scanning
	scan        bool
	scanChannel string

	// Candidates
	listCandidates bool
	candidateLimit int

	// Rule management
	listRules    bool
	setRule      string
	addRule      string
	removeRule   string

	// Filtering
	filterCandidates bool
	listFiltered     bool
	listRejected     bool

	// Performance tracking (Phase 3A)
	trackPerformance bool
	showStats        bool

	// Competitor monitoring (Phase 3B)
	competitorStats     bool
	trainingDataStatus  bool
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
	log.Println("Initialized database")

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
	log.Println("Initialized controller")

	if cfg.httpAddr != "" {
		if err := app.ServeHTTP(cfg.httpAddr, controller); err != nil {
			log.Fatal(err)
		}
		log.Println("Server is initialized")
		return
	}

	// Handle discovery and management modes
	switch {
	case cfg.addChannel != "":
		if err := addChannelCmd(ctx, store, downloader, cfg.addChannel, jsRuntime); err != nil {
			log.Fatal(err)
		}
		return
	case cfg.removeChannel != "":
		if err := store.DeactivateChannel(ctx, cfg.removeChannel); err != nil {
			log.Fatal(err)
		}
		log.Printf("Deactivated channel %s", cfg.removeChannel)
		return
	case cfg.listChannels:
		listChannelsCmd(ctx, store)
		return
	case cfg.scan:
		scanner := &app.Scanner{Store: store, Downloader: downloader, JSRuntime: jsRuntime}
		if err := scanner.ScanAllActive(ctx, cfg.limit); err != nil {
			log.Fatal(err)
		}
		return
	case cfg.scanChannel != "":
		scanner := &app.Scanner{Store: store, Downloader: downloader, JSRuntime: jsRuntime}
		count, err := scanner.ScanChannel(ctx, cfg.scanChannel, cfg.limit)
		if err != nil {
			log.Fatal(err)
		}
		log.Printf("Discovered %d videos", count)
		return
	case cfg.listCandidates:
		listCandidatesCmd(ctx, store, cfg.candidateLimit)
		return
	case cfg.listRules:
		listRulesCmd(ctx, store)
		return
	case cfg.setRule != "":
		if err := setRuleCmd(ctx, store, cfg.setRule); err != nil {
			log.Fatal(err)
		}
		return
	case cfg.addRule != "":
		if err := addRuleCmd(ctx, store, cfg.addRule); err != nil {
			log.Fatal(err)
		}
		return
	case cfg.removeRule != "":
		if err := store.DeleteRule(ctx, cfg.removeRule); err != nil {
			log.Fatal(err)
		}
		log.Printf("Removed rule: %s", cfg.removeRule)
		return
	case cfg.filterCandidates:
		filterCandidatesCmd(ctx, store, cfg.limit)
		return
	case cfg.listFiltered:
		listFilteredCmd(ctx, store, cfg.candidateLimit)
		return
	case cfg.listRejected:
		listRejectedCmd(ctx, store, cfg.candidateLimit)
		return
	case cfg.trackPerformance:
		if err := trackPerformanceCmd(ctx, store, cfg.dbPath); err != nil {
			log.Fatal(err)
		}
		return
	case cfg.showStats:
		showStatsCmd(ctx, store)
		return
	case cfg.competitorStats:
		showCompetitorStatsCmd(ctx, store)
		return
	case cfg.trainingDataStatus:
		showTrainingDataStatusCmd(ctx, store)
		return
	}

	// Handle sync modes
	log.Println("Handling downloading")
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

func addChannelCmd(ctx context.Context, store *app.SQLiteStore, downloader app.Downloader, channelInput string, jsRuntime string) error {
	channelURL := channelInput
	if !strings.Contains(channelInput, "youtube.com") && !strings.Contains(channelInput, "youtu.be") {
		// Assume it's a channel ID
		channelURL = "https://www.youtube.com/channel/" + channelInput
	}

	// Try to get channel metadata by fetching one video
	videos, err := downloader.GetChannelVideosMetadata(ctx, channelURL, 1, jsRuntime)
	var channelID, channelName string
	if err == nil && len(videos) > 0 {
		channelID = videos[0].ChannelID
		channelName = videos[0].ChannelTitle
	} else {
		// Fallback: extract channel ID from URL or use input
		channelID = extractChannelID(channelInput)
	}

	ch := app.Channel{
		ChannelID:          channelID,
		Name:               channelName,
		URL:                channelURL,
		ScanFrequencyHours: 6,
		IsActive:           true,
	}

	if err := store.AddChannel(ctx, ch); err != nil {
		return err
	}
	log.Printf("Added channel: %s (%s)", channelName, channelID)
	return nil
}

func extractChannelID(input string) string {
	// Try to extract channel ID from URL
	if strings.Contains(input, "/channel/") {
		parts := strings.Split(input, "/channel/")
		if len(parts) > 1 {
			id := strings.Split(parts[1], "/")[0]
			id = strings.Split(id, "?")[0]
			return id
		}
	}
	if strings.Contains(input, "/@") {
		parts := strings.Split(input, "/@")
		if len(parts) > 1 {
			handle := strings.Split(parts[1], "/")[0]
			handle = strings.Split(handle, "?")[0]
			return "@" + handle
		}
	}
	return input
}

func listChannelsCmd(ctx context.Context, store *app.SQLiteStore) {
	channels, err := store.ListActiveChannels(ctx)
	if err != nil {
		log.Fatal(err)
	}
	if len(channels) == 0 {
		log.Println("No channels in watchlist")
		return
	}
	fmt.Println("Watched channels:")
	for _, ch := range channels {
		lastScan := "never"
		if ch.LastScannedAt != nil {
			lastScan = ch.LastScannedAt.Format("2006-01-02 15:04")
		}
		fmt.Printf("  %s | %s | Last scanned: %s\n", ch.ChannelID, ch.Name, lastScan)
	}
}

func listCandidatesCmd(ctx context.Context, store *app.SQLiteStore, limit int) {
	candidates, err := store.ListPendingCandidates(ctx, limit)
	if err != nil {
		log.Fatal(err)
	}
	if len(candidates) == 0 {
		log.Println("No pending candidates")
		return
	}
	fmt.Println("Video candidates (not yet uploaded):")
	for _, c := range candidates {
		published := "unknown"
		if c.PublishedAt != nil {
			published = c.PublishedAt.Format("2006-01-02")
		}
		fmt.Printf("  %s | %s | Views: %d | Published: %s\n", c.VideoID, truncate(c.Title, 40), c.ViewCount, published)
	}
}

func truncate(s string, maxLen int) string {
	if len(s) <= maxLen {
		return s
	}
	return s[:maxLen-3] + "..."
}

func listRulesCmd(ctx context.Context, store *app.SQLiteStore) {
	// Seed default rules if none exist
	engine := app.NewRuleEngine(store)
	if err := engine.SeedDefaultRules(ctx); err != nil {
		log.Printf("Warning: failed to seed default rules: %v", err)
	}

	rules, err := store.ListAllRules(ctx)
	if err != nil {
		log.Fatal(err)
	}
	if len(rules) == 0 {
		log.Println("No filter rules configured")
		return
	}
	fmt.Println("Filter rules:")
	fmt.Println("  PRIORITY | NAME                  | TYPE      | FIELD           | VALUE                    | ACTIVE")
	fmt.Println("  " + strings.Repeat("-", 95))
	for _, r := range rules {
		active := "yes"
		if !r.IsActive {
			active = "no"
		}
		value := r.Value
		if len(value) > 24 {
			value = value[:21] + "..."
		}
		fmt.Printf("  %8d | %-21s | %-9s | %-15s | %-24s | %s\n",
			r.Priority, truncate(r.RuleName, 21), r.RuleType, r.Field, value, active)
	}
}

func setRuleCmd(ctx context.Context, store *app.SQLiteStore, setRule string) error {
	parts := strings.SplitN(setRule, "=", 2)
	if len(parts) != 2 {
		return fmt.Errorf("invalid format; use name=value")
	}
	ruleName := strings.TrimSpace(parts[0])
	ruleValue := strings.TrimSpace(parts[1])

	// Check if rule exists
	existing, err := store.GetRule(ctx, ruleName)
	if err != nil {
		return err
	}
	if existing == nil {
		return fmt.Errorf("rule %q not found; use --add-rule to create new rules", ruleName)
	}

	if err := store.UpdateRule(ctx, ruleName, ruleValue); err != nil {
		return err
	}
	log.Printf("Updated rule %s: %s -> %s", ruleName, existing.Value, ruleValue)
	return nil
}

func addRuleCmd(ctx context.Context, store *app.SQLiteStore, jsonStr string) error {
	rule, err := app.ParseRuleFromJSON(jsonStr)
	if err != nil {
		return fmt.Errorf("invalid rule JSON: %w", err)
	}

	if err := store.AddRule(ctx, *rule); err != nil {
		return err
	}
	log.Printf("Added rule: %s (type=%s, field=%s, value=%s)", rule.RuleName, rule.RuleType, rule.Field, rule.Value)
	return nil
}

func filterCandidatesCmd(ctx context.Context, store *app.SQLiteStore, limit int) {
	engine := app.NewRuleEngine(store)

	// Seed default rules if none exist
	if err := engine.SeedDefaultRules(ctx); err != nil {
		log.Printf("Warning: failed to seed default rules: %v", err)
	}

	passed, rejected, err := engine.FilterPendingCandidates(ctx, limit)
	if err != nil {
		log.Fatal(err)
	}

	if len(passed) == 0 && len(rejected) == 0 {
		log.Println("No pending candidates to filter")
		return
	}

	log.Printf("Filtered %d candidates: %d passed, %d rejected", len(passed)+len(rejected), len(passed), len(rejected))

	if len(passed) > 0 {
		fmt.Println("\nPassed:")
		for _, c := range passed {
			fmt.Printf("  %s | %s | Views: %d\n", c.VideoID, truncate(c.Title, 40), c.ViewCount)
		}
	}

	if len(rejected) > 0 {
		fmt.Println("\nRejected:")
		for _, c := range rejected {
			decision, _ := store.GetRuleDecision(ctx, c.VideoID)
			reason := "unknown"
			if decision != nil {
				reason = decision.RejectReason
			}
			fmt.Printf("  %s | %s | Reason: %s\n", c.VideoID, truncate(c.Title, 30), reason)
		}
	}
}

func listFilteredCmd(ctx context.Context, store *app.SQLiteStore, limit int) {
	candidates, err := store.ListFilteredCandidates(ctx, limit)
	if err != nil {
		log.Fatal(err)
	}
	if len(candidates) == 0 {
		log.Println("No candidates have passed filtering yet")
		return
	}
	fmt.Println("Candidates that passed filtering:")
	for _, c := range candidates {
		published := "unknown"
		if c.PublishedAt != nil {
			published = c.PublishedAt.Format("2006-01-02")
		}
		fmt.Printf("  %s | %s | Views: %d | Published: %s\n", c.VideoID, truncate(c.Title, 40), c.ViewCount, published)
	}
}

func listRejectedCmd(ctx context.Context, store *app.SQLiteStore, limit int) {
	rejected, err := store.ListRejectedCandidates(ctx, limit)
	if err != nil {
		log.Fatal(err)
	}
	if len(rejected) == 0 {
		log.Println("No candidates have been rejected yet")
		return
	}
	fmt.Println("Rejected candidates:")
	for _, r := range rejected {
		fmt.Printf("  %s | %s | Rejected by: %s | Reason: %s\n",
			r.VideoID, truncate(r.Title, 30), r.RejectRuleName, r.RejectReason)
	}
}

// trackPerformanceCmd invokes the Python Bilibili tracker to collect metrics.
func trackPerformanceCmd(ctx context.Context, store *app.SQLiteStore, dbPath string) error {
	// First, check if we have any uploads with bvid
	uploads, err := store.GetAllUploadsWithBvid(ctx)
	if err != nil {
		return fmt.Errorf("failed to get uploads: %w", err)
	}
	if len(uploads) == 0 {
		log.Println("No uploads with Bilibili bvid found. Upload videos first with the new uploader.")
		return nil
	}

	log.Printf("Found %d uploads with Bilibili bvid", len(uploads))

	// Try to run the Python tracker
	pythonPaths := []string{"python3", "python"}
	var pythonBinary string
	for _, p := range pythonPaths {
		if app.HasExecutable(p) {
			pythonBinary = p
			break
		}
	}
	if pythonBinary == "" {
		return fmt.Errorf("Python not found; install Python 3 to use --track-performance")
	}

	// Path to Python CLI
	trackerScript := "ml-service/app/cli.py"
	if _, err := os.Stat(trackerScript); os.IsNotExist(err) {
		return fmt.Errorf("tracker script not found at %s; ensure ml-service is set up", trackerScript)
	}

	// Run the tracker
	cmd := exec.CommandContext(ctx, pythonBinary, "-m", "app.cli", "track", "--db-path", dbPath)
	cmd.Dir = "ml-service"
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr

	log.Println("Running Bilibili performance tracker...")
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("tracker failed: %w", err)
	}

	// Also run labeling
	cmd = exec.CommandContext(ctx, pythonBinary, "-m", "app.cli", "label", "--db-path", dbPath)
	cmd.Dir = "ml-service"
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr

	log.Println("Running auto-labeling...")
	if err := cmd.Run(); err != nil {
		log.Printf("Warning: labeling failed: %v", err)
	}

	return nil
}

// showStatsCmd displays upload statistics.
func showStatsCmd(ctx context.Context, store *app.SQLiteStore) {
	stats, err := store.GetUploadStats(ctx)
	if err != nil {
		log.Fatalf("Failed to get stats: %v", err)
	}

	fmt.Println("\n========================================")
	fmt.Println("         Upload Statistics")
	fmt.Println("========================================")
	fmt.Printf("Total uploads:           %d\n", stats.TotalUploads)
	fmt.Printf("Uploads with Bilibili ID: %d\n", stats.UploadsWithBvid)
	fmt.Printf("Uploads with performance data: %d\n", stats.UploadsWithPerformance)
	fmt.Println()

	if len(stats.UploadsByLabel) > 0 {
		fmt.Println("By Label:")
		for label, count := range stats.UploadsByLabel {
			fmt.Printf("  %-12s: %d\n", label, count)
		}
		fmt.Println()
	}

	fmt.Println("Average Metrics (from latest checkpoint):")
	fmt.Printf("  Views:           %.0f\n", stats.AvgViews)
	fmt.Printf("  Likes:           %.0f\n", stats.AvgLikes)
	fmt.Printf("  Coins:           %.0f\n", stats.AvgCoins)
	fmt.Printf("  Engagement Rate: %.2f%%\n", stats.AvgEngagementRate*100)
	fmt.Println("========================================")

	// List recent uploads with performance
	recent, err := store.ListRecentUploadsWithPerformance(ctx, 10)
	if err != nil {
		log.Printf("Warning: failed to get recent uploads: %v", err)
		return
	}

	if len(recent) > 0 {
		fmt.Println("Recent Uploads with Performance:")
		fmt.Println("  VIDEO_ID        | BVID           | VIEWS    | LIKES | COINS | ENGAGEMENT | LABEL")
		fmt.Println("  " + strings.Repeat("-", 85))
		for _, u := range recent {
			label := u.Label
			if label == "" {
				label = "-"
			}
			fmt.Printf("  %-16s | %-14s | %8d | %5d | %5d | %9.2f%% | %s\n",
				truncate(u.VideoID, 16), truncate(u.BilibiliBvid, 14),
				u.Views, u.Likes, u.Coins, u.EngagementRate*100, label)
		}
		fmt.Println()
	}
}

// showCompetitorStatsCmd displays competitor channel statistics.
func showCompetitorStatsCmd(ctx context.Context, store *app.SQLiteStore) {
	stats, err := store.GetCompetitorStats(ctx)
	if err != nil {
		log.Fatalf("Failed to get competitor stats: %v", err)
	}

	fmt.Println("\n========================================")
	fmt.Println("     Competitor Channel Statistics")
	fmt.Println("========================================")
	fmt.Printf("Total channels:    %d\n", stats.TotalChannels)
	fmt.Printf("Active channels:   %d\n", stats.ActiveChannels)
	fmt.Printf("Total videos:      %d\n", stats.TotalVideos)
	fmt.Printf("Labeled videos:    %d\n", stats.LabeledVideos)
	fmt.Printf("Unlabeled videos:  %d\n", stats.UnlabeledVideos)
	fmt.Println("========================================")

	// List channels
	channels, err := store.ListCompetitorChannels(ctx)
	if err != nil {
		log.Printf("Warning: failed to list channels: %v", err)
		return
	}

	if len(channels) > 0 {
		fmt.Println("\nActive Competitor Channels:")
		fmt.Println("  UID          | NAME                     | FOLLOWERS  | VIDEOS")
		fmt.Println("  " + strings.Repeat("-", 65))
		for _, ch := range channels {
			fmt.Printf("  %-12s | %-24s | %10d | %6d\n",
				truncate(ch.BilibiliUID, 12), truncate(ch.Name, 24),
				ch.FollowerCount, ch.VideoCount)
		}
		fmt.Println()
	}
}

// showTrainingDataStatusCmd displays training data counts by label.
func showTrainingDataStatusCmd(ctx context.Context, store *app.SQLiteStore) {
	summary, err := store.GetTrainingDataSummary(ctx)
	if err != nil {
		log.Fatalf("Failed to get training data summary: %v", err)
	}

	fmt.Println("\n========================================")
	fmt.Println("       Training Data Summary")
	fmt.Println("========================================")
	fmt.Printf("Total videos:      %d\n", summary.Total)
	fmt.Println()
	fmt.Println("By Label:")
	fmt.Printf("  viral:      %d\n", summary.Viral)
	fmt.Printf("  successful: %d\n", summary.Successful)
	fmt.Printf("  standard:   %d\n", summary.Standard)
	fmt.Printf("  failed:     %d\n", summary.Failed)
	fmt.Printf("  unlabeled:  %d\n", summary.Unlabeled)
	fmt.Println("========================================")

	// Show percentage breakdown if we have data
	if summary.Total > 0 {
		labeled := summary.Total - summary.Unlabeled
		fmt.Printf("\nLabeled: %d (%.1f%%)\n", labeled, float64(labeled)/float64(summary.Total)*100)
		if labeled > 0 {
			fmt.Println("\nLabel Distribution (of labeled):")
			fmt.Printf("  viral:      %.1f%%\n", float64(summary.Viral)/float64(labeled)*100)
			fmt.Printf("  successful: %.1f%%\n", float64(summary.Successful)/float64(labeled)*100)
			fmt.Printf("  standard:   %.1f%%\n", float64(summary.Standard)/float64(labeled)*100)
			fmt.Printf("  failed:     %.1f%%\n", float64(summary.Failed)/float64(labeled)*100)
		}
		fmt.Println()
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

	// Channel management flags
	fs.StringVar(&cfg.addChannel, "add-channel", "", "Add a channel to watchlist (URL or ID)")
	fs.StringVar(&cfg.removeChannel, "remove-channel", "", "Remove a channel from watchlist")
	fs.BoolVar(&cfg.listChannels, "list-channels", false, "List all watched channels")

	// Scanning flags
	fs.BoolVar(&cfg.scan, "scan", false, "Scan watched channels for new videos")
	fs.StringVar(&cfg.scanChannel, "scan-channel", "", "Scan a specific channel")

	// Candidate flags
	fs.BoolVar(&cfg.listCandidates, "list-candidates", false, "List discovered video candidates")
	fs.IntVar(&cfg.candidateLimit, "candidate-limit", 20, "Limit for candidate listing")

	// Rule management flags
	fs.BoolVar(&cfg.listRules, "list-rules", false, "List all filter rules")
	fs.StringVar(&cfg.setRule, "set-rule", "", "Set/update a rule value (name=value)")
	fs.StringVar(&cfg.addRule, "add-rule", "", "Add a filter rule (JSON format)")
	fs.StringVar(&cfg.removeRule, "remove-rule", "", "Remove a filter rule by name")

	// Filtering flags
	fs.BoolVar(&cfg.filterCandidates, "filter", false, "Run rule filter on pending candidates")
	fs.BoolVar(&cfg.listFiltered, "list-filtered", false, "List candidates that passed filtering")
	fs.BoolVar(&cfg.listRejected, "list-rejected", false, "List candidates rejected by rules")

	// Performance tracking flags (Phase 3A)
	fs.BoolVar(&cfg.trackPerformance, "track-performance", false, "Collect Bilibili performance metrics for uploaded videos")
	fs.BoolVar(&cfg.showStats, "stats", false, "Show upload statistics summary")

	// Competitor monitoring flags (Phase 3B)
	fs.BoolVar(&cfg.competitorStats, "competitor-stats", false, "Show competitor channel statistics")
	fs.BoolVar(&cfg.trainingDataStatus, "training-data-status", false, "Show training data counts by label")

	if err := fs.Parse(args); err != nil {
		return cfg, err
	}

	// Determine if any discovery mode is active
	discoveryMode := cfg.addChannel != "" || cfg.removeChannel != "" || cfg.listChannels ||
		cfg.scan || cfg.scanChannel != "" || cfg.listCandidates ||
		cfg.listRules || cfg.setRule != "" || cfg.addRule != "" || cfg.removeRule != "" ||
		cfg.filterCandidates || cfg.listFiltered || cfg.listRejected ||
		cfg.trackPerformance || cfg.showStats ||
		cfg.competitorStats || cfg.trainingDataStatus

	if cfg.httpAddr == "" && cfg.channelID == "" && cfg.videoID == "" && !discoveryMode {
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
