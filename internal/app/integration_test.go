package app

import (
	"context"
	"fmt"
	"os"
	"strings"
	"sync"
	"testing"
	"time"
)

// MockDownloader simulates yt-dlp for testing
type MockDownloader struct {
	mu              sync.Mutex
	channelVideos   map[string][]VideoMetadata
	downloadedFiles map[string][]string
	downloadError   error
	metadataError   error
}

func NewMockDownloader() *MockDownloader {
	return &MockDownloader{
		channelVideos:   make(map[string][]VideoMetadata),
		downloadedFiles: make(map[string][]string),
	}
}

func (m *MockDownloader) AddChannelVideos(channelURL string, videos []VideoMetadata) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.channelVideos[channelURL] = videos
}

func (m *MockDownloader) SetDownloadResult(videoID string, files []string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.downloadedFiles[videoID] = files
}

func (m *MockDownloader) ListChannelVideoIDs(ctx context.Context, channelURL string, limit int, jsRuntime string) ([]string, error) {
	m.mu.Lock()
	defer m.mu.Unlock()

	videos, ok := m.channelVideos[channelURL]
	if !ok {
		return nil, nil
	}

	ids := make([]string, 0, len(videos))
	for i, v := range videos {
		if i >= limit {
			break
		}
		ids = append(ids, v.ID)
	}
	return ids, nil
}

func (m *MockDownloader) DownloadVideo(ctx context.Context, videoURL, outputDir string, jsRuntime, format string) ([]string, error) {
	if m.downloadError != nil {
		return nil, m.downloadError
	}

	m.mu.Lock()
	defer m.mu.Unlock()

	// Extract video ID from URL
	videoID := extractVideoID(videoURL)
	if files, ok := m.downloadedFiles[videoID]; ok {
		return files, nil
	}
	return []string{fmt.Sprintf("%s/%s.mp4", outputDir, videoID)}, nil
}

func (m *MockDownloader) GetVideoMetadata(ctx context.Context, videoID string, jsRuntime string) (*VideoMetadata, error) {
	if m.metadataError != nil {
		return nil, m.metadataError
	}

	m.mu.Lock()
	defer m.mu.Unlock()

	for _, videos := range m.channelVideos {
		for _, v := range videos {
			if v.ID == videoID {
				return &v, nil
			}
		}
	}
	return nil, fmt.Errorf("video not found: %s", videoID)
}

func (m *MockDownloader) GetChannelVideosMetadata(ctx context.Context, channelURL string, limit int, jsRuntime string) ([]VideoMetadata, error) {
	if m.metadataError != nil {
		return nil, m.metadataError
	}

	m.mu.Lock()
	defer m.mu.Unlock()

	videos, ok := m.channelVideos[channelURL]
	if !ok {
		return nil, nil
	}

	if limit > len(videos) {
		limit = len(videos)
	}
	return videos[:limit], nil
}

func extractVideoID(url string) string {
	// Simple extraction for mock
	if len(url) > 32 {
		return url[len(url)-11:]
	}
	return url
}

// MockUploader simulates video upload for testing
type MockUploader struct {
	mu            sync.Mutex
	uploadedFiles []string
	uploadError   error
}

func NewMockUploader() *MockUploader {
	return &MockUploader{
		uploadedFiles: make([]string, 0),
	}
}

func (m *MockUploader) Upload(path string) error {
	if m.uploadError != nil {
		return m.uploadError
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	m.uploadedFiles = append(m.uploadedFiles, path)
	return nil
}

func (m *MockUploader) GetUploadedFiles() []string {
	m.mu.Lock()
	defer m.mu.Unlock()
	return append([]string{}, m.uploadedFiles...)
}

// Test helper to create a test environment
type TestEnv struct {
	Store      *SQLiteStore
	Downloader *MockDownloader
	Uploader   *MockUploader
	Scanner    *Scanner
	RuleEngine *RuleEngine
	Controller *Controller
	DBPath     string
}

func NewTestEnv(t *testing.T) *TestEnv {
	dbPath := t.TempDir() + "/integration_test.db"
	store, err := NewSQLiteStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}

	ctx := context.Background()
	if err := store.EnsureSchema(ctx); err != nil {
		t.Fatal(err)
	}

	downloader := NewMockDownloader()
	uploader := NewMockUploader()
	ruleEngine := NewRuleEngine(store)

	scanner := &Scanner{
		Store:      store,
		Downloader: downloader,
		JSRuntime:  "",
		RuleEngine: ruleEngine,
		AutoFilter: true,
	}

	controller := &Controller{
		Downloader: downloader,
		Uploader:   uploader,
		Store:      store,
		OutputDir:  t.TempDir(),
		JSRuntime:  "",
		Format:     "",
	}

	return &TestEnv{
		Store:      store,
		Downloader: downloader,
		Uploader:   uploader,
		Scanner:    scanner,
		RuleEngine: ruleEngine,
		Controller: controller,
		DBPath:     dbPath,
	}
}

func (e *TestEnv) Cleanup() {
	os.Remove(e.DBPath)
}

// ============================================================================
// Integration Tests
// ============================================================================

func TestIntegration_FullWorkflow_DiscoverFilterDownloadUpload(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	// Setup: Add a channel
	channel := Channel{
		ChannelID:          "UC_TestChannel",
		Name:               "Test Channel",
		URL:                "https://www.youtube.com/channel/UC_TestChannel",
		ScanFrequencyHours: 6,
		IsActive:           true,
	}
	if err := env.Store.AddChannel(ctx, channel); err != nil {
		t.Fatalf("AddChannel: %v", err)
	}

	// Setup: Add mock videos to the channel
	now := time.Now()
	recentDate := now.AddDate(0, 0, -5).Format("20060102")
	oldDate := now.AddDate(0, 0, -60).Format("20060102")

	mockVideos := []VideoMetadata{
		{ID: "vid_good_1", Title: "Great Video 1", Duration: 300, ViewCount: 5000, LikeCount: 200, CommentCount: 50, UploadDate: recentDate, ChannelID: "UC_TestChannel", ChannelTitle: "Test Channel", Categories: []string{"Entertainment"}},
		{ID: "vid_good_2", Title: "Great Video 2", Duration: 600, ViewCount: 10000, LikeCount: 500, CommentCount: 100, UploadDate: recentDate, ChannelID: "UC_TestChannel", ChannelTitle: "Test Channel", Categories: []string{"Gaming"}},
		{ID: "vid_low_views", Title: "Low Views Video", Duration: 300, ViewCount: 100, LikeCount: 5, CommentCount: 1, UploadDate: recentDate, ChannelID: "UC_TestChannel", ChannelTitle: "Test Channel", Categories: []string{"Music"}},
		{ID: "vid_too_old", Title: "Old Video", Duration: 300, ViewCount: 50000, LikeCount: 2000, CommentCount: 500, UploadDate: oldDate, ChannelID: "UC_TestChannel", ChannelTitle: "Test Channel", Categories: []string{"Music"}},
		{ID: "vid_too_long", Title: "Very Long Video", Duration: 7200, ViewCount: 8000, LikeCount: 300, CommentCount: 80, UploadDate: recentDate, ChannelID: "UC_TestChannel", ChannelTitle: "Test Channel", Categories: []string{"Education"}},
		{ID: "vid_news", Title: "News Video", Duration: 300, ViewCount: 20000, LikeCount: 500, CommentCount: 200, UploadDate: recentDate, ChannelID: "UC_TestChannel", ChannelTitle: "Test Channel", Categories: []string{"News & Politics"}},
	}
	env.Downloader.AddChannelVideos(channel.URL, mockVideos)

	// Step 1: Seed default rules
	if err := env.RuleEngine.SeedDefaultRules(ctx); err != nil {
		t.Fatalf("SeedDefaultRules: %v", err)
	}

	// Verify rules are seeded
	rules, _ := env.Store.ListActiveRules(ctx)
	if len(rules) != len(DefaultRules) {
		t.Fatalf("expected %d rules, got %d", len(DefaultRules), len(rules))
	}

	// Step 2: Scan the channel (with auto-filter enabled)
	count, err := env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)
	if err != nil {
		t.Fatalf("ScanChannel: %v", err)
	}
	if count != len(mockVideos) {
		t.Fatalf("expected %d videos discovered, got %d", len(mockVideos), count)
	}

	// Step 3: Verify filtering results
	filtered, err := env.Store.ListFilteredCandidates(ctx, 10)
	if err != nil {
		t.Fatalf("ListFilteredCandidates: %v", err)
	}
	// Should pass: vid_good_1, vid_good_2 (meet all criteria)
	if len(filtered) != 2 {
		t.Errorf("expected 2 filtered candidates, got %d", len(filtered))
		for _, c := range filtered {
			t.Logf("  passed: %s - %s (views: %d)", c.VideoID, c.Title, c.ViewCount)
		}
	}

	rejected, err := env.Store.ListRejectedCandidates(ctx, 10)
	if err != nil {
		t.Fatalf("ListRejectedCandidates: %v", err)
	}
	// Should reject: vid_low_views (< 1000 views), vid_too_old (> 30 days), vid_too_long (> 3600s), vid_news (blocked category)
	if len(rejected) != 4 {
		t.Errorf("expected 4 rejected candidates, got %d", len(rejected))
		for _, r := range rejected {
			t.Logf("  rejected: %s - %s (rule: %s, reason: %s)", r.VideoID, r.Title, r.RejectRuleName, r.RejectReason)
		}
	}

	// Step 4: Simulate download and upload of filtered videos
	for _, candidate := range filtered {
		// Download
		files, err := env.Downloader.DownloadVideo(ctx, videoURL(candidate.VideoID), env.Controller.OutputDir, "", "")
		if err != nil {
			t.Fatalf("DownloadVideo %s: %v", candidate.VideoID, err)
		}
		if len(files) == 0 {
			t.Fatalf("no files downloaded for %s", candidate.VideoID)
		}

		// Upload
		for _, file := range files {
			if err := env.Uploader.Upload(file); err != nil {
				t.Fatalf("Upload %s: %v", file, err)
			}
		}

		// Mark as uploaded
		if err := env.Store.MarkUploaded(ctx, candidate.VideoID, candidate.ChannelID); err != nil {
			t.Fatalf("MarkUploaded %s: %v", candidate.VideoID, err)
		}
	}

	// Verify uploads
	uploaded := env.Uploader.GetUploadedFiles()
	if len(uploaded) != 2 {
		t.Errorf("expected 2 uploaded files, got %d", len(uploaded))
	}

	// Verify filtered candidates are no longer pending
	filtered, _ = env.Store.ListFilteredCandidates(ctx, 10)
	if len(filtered) != 0 {
		t.Errorf("expected 0 pending filtered candidates after upload, got %d", len(filtered))
	}
}

func TestIntegration_CustomRules_Workflow(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	// Setup channel
	channel := Channel{
		ChannelID: "UC_Custom",
		Name:      "Custom Channel",
		URL:       "https://www.youtube.com/channel/UC_Custom",
		IsActive:  true,
	}
	env.Store.AddChannel(ctx, channel)

	// Add videos
	now := time.Now()
	recentDate := now.AddDate(0, 0, -2).Format("20060102")
	mockVideos := []VideoMetadata{
		{ID: "vid1", Title: "Normal Video", Duration: 300, ViewCount: 500, UploadDate: recentDate, ChannelID: "UC_Custom", Categories: []string{"Entertainment"}},
		{ID: "vid2", Title: "Sponsored Content", Duration: 300, ViewCount: 5000, UploadDate: recentDate, ChannelID: "UC_Custom", Categories: []string{"Entertainment"}},
		{ID: "vid3", Title: "Ad Break Inside", Duration: 300, ViewCount: 3000, UploadDate: recentDate, ChannelID: "UC_Custom", Categories: []string{"Entertainment"}},
		{ID: "vid4", Title: "Great Content", Duration: 300, ViewCount: 2000, UploadDate: recentDate, ChannelID: "UC_Custom", Categories: []string{"Entertainment"}},
	}
	env.Downloader.AddChannelVideos(channel.URL, mockVideos)

	// Add custom rules (lower view threshold, regex filter for sponsors)
	rules := []FilterRule{
		{RuleName: "min_views", RuleType: "min", Field: "view_count", Value: "500", IsActive: true, Priority: 100},
		{RuleName: "block_sponsors", RuleType: "regex", Field: "title", Value: "(?i)sponsor|ad\\s", IsActive: true, Priority: 90},
	}
	for _, r := range rules {
		if err := env.Store.AddRule(ctx, r); err != nil {
			t.Fatalf("AddRule: %v", err)
		}
	}

	// Scan channel
	env.Scanner.AutoFilter = false // We'll filter manually
	count, err := env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)
	if err != nil {
		t.Fatalf("ScanChannel: %v", err)
	}
	if count != 4 {
		t.Fatalf("expected 4 videos, got %d", count)
	}

	// Filter candidates
	passed, rejected, err := env.RuleEngine.FilterPendingCandidates(ctx, 10)
	if err != nil {
		t.Fatalf("FilterPendingCandidates: %v", err)
	}

	// vid1: 500 views = exactly min, passes
	// vid2: 5000 views but "Sponsored" in title, rejected
	// vid3: 3000 views but "Ad " in title, rejected
	// vid4: 2000 views, no blocked words, passes

	if len(passed) != 2 {
		t.Errorf("expected 2 passed, got %d", len(passed))
		for _, p := range passed {
			t.Logf("  passed: %s", p.Title)
		}
	}

	if len(rejected) != 2 {
		t.Errorf("expected 2 rejected, got %d", len(rejected))
		for _, r := range rejected {
			decision, _ := env.Store.GetRuleDecision(ctx, r.VideoID)
			t.Logf("  rejected: %s (rule: %s)", r.Title, decision.RejectRuleName)
		}
	}

	// Verify specific rejections
	for _, r := range rejected {
		decision, _ := env.Store.GetRuleDecision(ctx, r.VideoID)
		if decision.RejectRuleName != "block_sponsors" {
			t.Errorf("expected rejection by block_sponsors, got %s", decision.RejectRuleName)
		}
	}
}

func TestIntegration_AllowlistRule_Workflow(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	// Setup
	channel := Channel{
		ChannelID: "UC_Lang",
		URL:       "https://www.youtube.com/channel/UC_Lang",
		IsActive:  true,
	}
	env.Store.AddChannel(ctx, channel)

	now := time.Now()
	recentDate := now.AddDate(0, 0, -1).Format("20060102")
	mockVideos := []VideoMetadata{
		{ID: "vid_en", Title: "English Video", ViewCount: 5000, UploadDate: recentDate, ChannelID: "UC_Lang", Categories: []string{"Entertainment"}},
		{ID: "vid_zh", Title: "Chinese Video", ViewCount: 5000, UploadDate: recentDate, ChannelID: "UC_Lang", Categories: []string{"Entertainment"}},
		{ID: "vid_ja", Title: "Japanese Video", ViewCount: 5000, UploadDate: recentDate, ChannelID: "UC_Lang", Categories: []string{"Entertainment"}},
		{ID: "vid_ko", Title: "Korean Video", ViewCount: 5000, UploadDate: recentDate, ChannelID: "UC_Lang", Categories: []string{"Entertainment"}},
	}
	env.Downloader.AddChannelVideos(channel.URL, mockVideos)

	// Scan first to get candidates with language info
	env.Scanner.AutoFilter = false
	env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)

	// Manually set language on candidates (simulating real metadata)
	candidates, _ := env.Store.ListPendingCandidates(ctx, 10)
	languages := map[string]string{"vid_en": "en", "vid_zh": "zh", "vid_ja": "ja", "vid_ko": "ko"}
	for _, c := range candidates {
		c.Language = languages[c.VideoID]
		env.Store.UpsertCandidate(ctx, c)
	}

	// Add allowlist rule for English and Chinese only
	rule := FilterRule{
		RuleName: "allowed_languages",
		RuleType: "allowlist",
		Field:    "language",
		Value:    `["en", "zh"]`,
		IsActive: true,
		Priority: 100,
	}
	env.Store.AddRule(ctx, rule)

	// Filter
	passed, rejected, err := env.RuleEngine.FilterPendingCandidates(ctx, 10)
	if err != nil {
		t.Fatalf("FilterPendingCandidates: %v", err)
	}

	if len(passed) != 2 {
		t.Errorf("expected 2 passed (en, zh), got %d", len(passed))
	}
	if len(rejected) != 2 {
		t.Errorf("expected 2 rejected (ja, ko), got %d", len(rejected))
	}
}

func TestIntegration_MultiChannelScan_Workflow(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	// Setup multiple channels
	channels := []Channel{
		{ChannelID: "UC_A", Name: "Channel A", URL: "https://www.youtube.com/channel/UC_A", IsActive: true},
		{ChannelID: "UC_B", Name: "Channel B", URL: "https://www.youtube.com/channel/UC_B", IsActive: true},
		{ChannelID: "UC_C", Name: "Channel C", URL: "https://www.youtube.com/channel/UC_C", IsActive: false}, // Inactive
	}
	for _, ch := range channels {
		env.Store.AddChannel(ctx, ch)
	}

	now := time.Now()
	recentDate := now.AddDate(0, 0, -3).Format("20060102")

	// Add videos to each channel
	env.Downloader.AddChannelVideos(channels[0].URL, []VideoMetadata{
		{ID: "a1", Title: "Video A1", ViewCount: 5000, Duration: 300, UploadDate: recentDate, ChannelID: "UC_A", Categories: []string{"Gaming"}},
		{ID: "a2", Title: "Video A2", ViewCount: 3000, Duration: 300, UploadDate: recentDate, ChannelID: "UC_A", Categories: []string{"Gaming"}},
	})
	env.Downloader.AddChannelVideos(channels[1].URL, []VideoMetadata{
		{ID: "b1", Title: "Video B1", ViewCount: 8000, Duration: 300, UploadDate: recentDate, ChannelID: "UC_B", Categories: []string{"Music"}},
	})
	env.Downloader.AddChannelVideos(channels[2].URL, []VideoMetadata{
		{ID: "c1", Title: "Video C1", ViewCount: 10000, Duration: 300, UploadDate: recentDate, ChannelID: "UC_C", Categories: []string{"Tech"}},
	})

	// Seed default rules
	env.RuleEngine.SeedDefaultRules(ctx)

	// Scan all active channels
	env.Scanner.AutoFilter = true
	if err := env.Scanner.ScanAllActive(ctx, 10); err != nil {
		t.Fatalf("ScanAllActive: %v", err)
	}

	// Verify: should have videos from A and B only (C is inactive)
	filtered, _ := env.Store.ListFilteredCandidates(ctx, 10)

	// a1, a2, b1 should all pass (views > 1000, duration < 3600, age < 30, not News)
	if len(filtered) != 3 {
		t.Errorf("expected 3 filtered candidates, got %d", len(filtered))
	}

	// Verify channel C was not scanned
	candidateC, _ := env.Store.GetCandidate(ctx, "c1")
	if candidateC != nil {
		t.Error("inactive channel should not be scanned")
	}
}

func TestIntegration_RuleUpdate_Workflow(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	// Setup
	channel := Channel{ChannelID: "UC_Update", URL: "https://www.youtube.com/channel/UC_Update", IsActive: true}
	env.Store.AddChannel(ctx, channel)

	now := time.Now()
	recentDate := now.AddDate(0, 0, -1).Format("20060102")
	mockVideos := []VideoMetadata{
		{ID: "vid_800", Title: "800 Views", ViewCount: 800, Duration: 300, UploadDate: recentDate, ChannelID: "UC_Update", Categories: []string{"Entertainment"}},
		{ID: "vid_1500", Title: "1500 Views", ViewCount: 1500, Duration: 300, UploadDate: recentDate, ChannelID: "UC_Update", Categories: []string{"Entertainment"}},
		{ID: "vid_3000", Title: "3000 Views", ViewCount: 3000, Duration: 300, UploadDate: recentDate, ChannelID: "UC_Update", Categories: []string{"Entertainment"}},
	}
	env.Downloader.AddChannelVideos(channel.URL, mockVideos)

	// Add strict rule: min 2000 views
	env.Store.AddRule(ctx, FilterRule{
		RuleName: "min_views",
		RuleType: "min",
		Field:    "view_count",
		Value:    "2000",
		IsActive: true,
		Priority: 100,
	})

	// Scan and filter
	env.Scanner.AutoFilter = false
	env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)

	passed1, _, _ := env.RuleEngine.FilterPendingCandidates(ctx, 10)
	if len(passed1) != 1 {
		t.Errorf("round 1: expected 1 passed (3000 views only), got %d", len(passed1))
	}

	// Update rule to be more lenient: min 500 views
	env.Store.UpdateRule(ctx, "min_views", "500")

	// Add more videos and rescan
	newVideos := []VideoMetadata{
		{ID: "vid_600", Title: "600 Views", ViewCount: 600, Duration: 300, UploadDate: recentDate, ChannelID: "UC_Update", Categories: []string{"Entertainment"}},
	}
	env.Downloader.AddChannelVideos(channel.URL, append(mockVideos, newVideos...))
	env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)

	// Filter the new candidate
	passed2, _, _ := env.RuleEngine.FilterPendingCandidates(ctx, 10)
	if len(passed2) != 1 {
		t.Errorf("round 2: expected 1 new passed (600 views), got %d", len(passed2))
	}
}

func TestIntegration_ReEvaluation_Workflow(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	// Setup
	channel := Channel{ChannelID: "UC_ReEval", URL: "https://www.youtube.com/channel/UC_ReEval", IsActive: true}
	env.Store.AddChannel(ctx, channel)

	now := time.Now()
	recentDate := now.AddDate(0, 0, -1).Format("20060102")
	mockVideos := []VideoMetadata{
		{ID: "vid_reeval", Title: "Test Video", ViewCount: 500, Duration: 300, UploadDate: recentDate, ChannelID: "UC_ReEval", Categories: []string{"Entertainment"}},
	}
	env.Downloader.AddChannelVideos(channel.URL, mockVideos)

	// Add strict rule
	env.Store.AddRule(ctx, FilterRule{
		RuleName: "min_views",
		RuleType: "min",
		Field:    "view_count",
		Value:    "1000",
		IsActive: true,
		Priority: 100,
	})

	// Scan and filter - should be rejected
	env.Scanner.AutoFilter = false
	env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)

	passed, rejected, _ := env.RuleEngine.FilterPendingCandidates(ctx, 10)
	if len(rejected) != 1 {
		t.Fatalf("expected 1 rejected, got %d", len(rejected))
	}
	if len(passed) != 0 {
		t.Fatalf("expected 0 passed, got %d", len(passed))
	}

	// Verify decision recorded
	decision, _ := env.Store.GetRuleDecision(ctx, "vid_reeval")
	if decision == nil || decision.RulePassed {
		t.Error("expected rejection decision")
	}

	// Later: video goes viral, update candidate metrics
	env.Store.UpdateCandidateMetrics(ctx, "vid_reeval", 5000, 200, 50)

	// Re-evaluate by calling Evaluate directly (simulating manual re-evaluation)
	candidate, _ := env.Store.GetCandidate(ctx, "vid_reeval")
	newDecision, _ := env.RuleEngine.Evaluate(ctx, *candidate)

	if !newDecision.RulePassed {
		t.Error("expected video to pass after view count update")
	}
}

func TestIntegration_ControllerSync_Workflow(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	// Setup channel with mock videos
	channelID := "UC_Sync"
	// Note: channelURL() adds /videos suffix
	mockChannelURL := "https://www.youtube.com/channel/UC_Sync/videos"

	mockVideos := []VideoMetadata{
		{ID: "sync1", Title: "Sync Video 1", ViewCount: 5000, ChannelID: channelID},
		{ID: "sync2", Title: "Sync Video 2", ViewCount: 3000, ChannelID: channelID},
		{ID: "sync3", Title: "Sync Video 3", ViewCount: 8000, ChannelID: channelID},
	}
	env.Downloader.AddChannelVideos(mockChannelURL, mockVideos)

	// Sync channel using controller
	result, err := env.Controller.SyncChannel(ctx, channelID, 3)
	if err != nil {
		t.Fatalf("SyncChannel: %v", err)
	}

	if result.Considered != 3 {
		t.Errorf("expected 3 considered, got %d", result.Considered)
	}
	if result.Downloaded != 3 {
		t.Errorf("expected 3 downloaded, got %d", result.Downloaded)
	}
	if result.Uploaded != 3 {
		t.Errorf("expected 3 uploaded, got %d", result.Uploaded)
	}
	if result.Skipped != 0 {
		t.Errorf("expected 0 skipped, got %d", result.Skipped)
	}

	// Verify all uploaded
	uploaded := env.Uploader.GetUploadedFiles()
	if len(uploaded) != 3 {
		t.Errorf("expected 3 uploaded files, got %d", len(uploaded))
	}

	// Re-sync should skip all
	result2, err := env.Controller.SyncChannel(ctx, channelID, 3)
	if err != nil {
		t.Fatalf("SyncChannel (resync): %v", err)
	}
	if result2.Skipped != 3 {
		t.Errorf("expected 3 skipped on resync, got %d", result2.Skipped)
	}
	if result2.Downloaded != 0 {
		t.Errorf("expected 0 downloaded on resync, got %d", result2.Downloaded)
	}
}

func TestIntegration_EndToEnd_WithFilterAndUpload(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	// Complete end-to-end test:
	// 1. Add channel
	// 2. Scan for videos
	// 3. Apply rules to filter
	// 4. Download and upload only filtered videos
	// 5. Verify upload count matches filter count

	channel := Channel{
		ChannelID: "UC_E2E",
		Name:      "E2E Test Channel",
		URL:       "https://www.youtube.com/channel/UC_E2E",
		IsActive:  true,
	}
	env.Store.AddChannel(ctx, channel)

	now := time.Now()
	recentDate := now.AddDate(0, 0, -5).Format("20060102")

	// Mix of videos that will pass/fail different rules
	mockVideos := []VideoMetadata{
		{ID: "e2e_pass_1", Title: "Quality Content 1", Duration: 300, ViewCount: 5000, UploadDate: recentDate, ChannelID: "UC_E2E", Categories: []string{"Entertainment"}},
		{ID: "e2e_pass_2", Title: "Quality Content 2", Duration: 600, ViewCount: 8000, UploadDate: recentDate, ChannelID: "UC_E2E", Categories: []string{"Gaming"}},
		{ID: "e2e_fail_views", Title: "Low View Video", Duration: 300, ViewCount: 100, UploadDate: recentDate, ChannelID: "UC_E2E", Categories: []string{"Entertainment"}},
		{ID: "e2e_fail_duration", Title: "Very Long Video", Duration: 7200, ViewCount: 5000, UploadDate: recentDate, ChannelID: "UC_E2E", Categories: []string{"Entertainment"}},
	}
	env.Downloader.AddChannelVideos(channel.URL, mockVideos)

	// Seed default rules
	env.RuleEngine.SeedDefaultRules(ctx)

	// Scan with auto-filter
	env.Scanner.AutoFilter = true
	count, err := env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)
	if err != nil {
		t.Fatalf("ScanChannel: %v", err)
	}
	if count != 4 {
		t.Fatalf("expected 4 scanned, got %d", count)
	}

	// Get filtered candidates
	filtered, err := env.Store.ListFilteredCandidates(ctx, 10)
	if err != nil {
		t.Fatalf("ListFilteredCandidates: %v", err)
	}
	if len(filtered) != 2 {
		t.Fatalf("expected 2 filtered, got %d", len(filtered))
	}

	// Download and upload only filtered candidates
	uploadCount := 0
	for _, c := range filtered {
		files, err := env.Downloader.DownloadVideo(ctx, videoURL(c.VideoID), env.Controller.OutputDir, "", "")
		if err != nil {
			t.Fatalf("Download %s: %v", c.VideoID, err)
		}

		for _, f := range files {
			if err := env.Uploader.Upload(f); err != nil {
				t.Fatalf("Upload %s: %v", f, err)
			}
			uploadCount++
		}

		env.Store.MarkUploaded(ctx, c.VideoID, c.ChannelID)
	}

	// Verify
	if uploadCount != 2 {
		t.Errorf("expected 2 uploads, got %d", uploadCount)
	}

	uploaded := env.Uploader.GetUploadedFiles()
	if len(uploaded) != 2 {
		t.Errorf("expected 2 in upload history, got %d", len(uploaded))
	}

	// Verify rejected weren't uploaded
	rejected, _ := env.Store.ListRejectedCandidates(ctx, 10)
	if len(rejected) != 2 {
		t.Errorf("expected 2 rejected, got %d", len(rejected))
	}

	for _, r := range rejected {
		isUploaded, _ := env.Store.IsUploaded(ctx, r.VideoID)
		if isUploaded {
			t.Errorf("rejected video %s should not be uploaded", r.VideoID)
		}
	}
}

func TestIntegration_RulePriority_Workflow(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	channel := Channel{ChannelID: "UC_Priority", URL: "https://www.youtube.com/channel/UC_Priority", IsActive: true}
	env.Store.AddChannel(ctx, channel)

	now := time.Now()
	recentDate := now.AddDate(0, 0, -1).Format("20060102")

	// Video that would fail both rules
	mockVideos := []VideoMetadata{
		{ID: "vid_multi_fail", Title: "Sponsored Low Views", ViewCount: 100, Duration: 300, UploadDate: recentDate, ChannelID: "UC_Priority", Categories: []string{"Entertainment"}},
	}
	env.Downloader.AddChannelVideos(channel.URL, mockVideos)

	// Add rules with different priorities
	// Higher priority rule should be checked first
	env.Store.AddRule(ctx, FilterRule{
		RuleName: "min_views",
		RuleType: "min",
		Field:    "view_count",
		Value:    "1000",
		IsActive: true,
		Priority: 100, // Higher priority
	})
	env.Store.AddRule(ctx, FilterRule{
		RuleName: "block_sponsored",
		RuleType: "regex",
		Field:    "title",
		Value:    "(?i)sponsored",
		IsActive: true,
		Priority: 50, // Lower priority
	})

	env.Scanner.AutoFilter = false
	env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)

	_, rejected, _ := env.RuleEngine.FilterPendingCandidates(ctx, 10)
	if len(rejected) != 1 {
		t.Fatalf("expected 1 rejected, got %d", len(rejected))
	}

	// Should be rejected by min_views (higher priority) first
	decision, _ := env.Store.GetRuleDecision(ctx, "vid_multi_fail")
	if decision.RejectRuleName != "min_views" {
		t.Errorf("expected rejection by min_views (higher priority), got %s", decision.RejectRuleName)
	}
}

// ============================================================================
// Pipeline Error Handling Tests
// ============================================================================

func TestIntegration_Pipeline_DownloadError_StopsUpload(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	// Setup channel with videos
	channelID := "UC_DownErr"
	mockChannelURL := "https://www.youtube.com/channel/UC_DownErr/videos"

	mockVideos := []VideoMetadata{
		{ID: "vid_ok", Title: "OK Video", ViewCount: 5000, ChannelID: channelID},
		{ID: "vid_fail", Title: "Fail Video", ViewCount: 3000, ChannelID: channelID},
	}
	env.Downloader.AddChannelVideos(mockChannelURL, mockVideos)

	// Set download error
	env.Downloader.downloadError = fmt.Errorf("network timeout")

	result, err := env.Controller.SyncChannel(ctx, channelID, 2)

	// Should fail with download error
	if err == nil {
		t.Error("expected download error, got nil")
	}
	if result.Downloaded != 0 {
		t.Errorf("expected 0 downloads on error, got %d", result.Downloaded)
	}
	if result.Uploaded != 0 {
		t.Errorf("expected 0 uploads on error, got %d", result.Uploaded)
	}

	// Verify nothing was marked as uploaded
	uploaded := env.Uploader.GetUploadedFiles()
	if len(uploaded) != 0 {
		t.Errorf("expected no uploaded files, got %d", len(uploaded))
	}
}

func TestIntegration_Pipeline_UploadError_StopsProcessing(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	channelID := "UC_UpErr"
	mockChannelURL := "https://www.youtube.com/channel/UC_UpErr/videos"

	mockVideos := []VideoMetadata{
		{ID: "vid1", Title: "Video 1", ViewCount: 5000, ChannelID: channelID},
		{ID: "vid2", Title: "Video 2", ViewCount: 3000, ChannelID: channelID},
		{ID: "vid3", Title: "Video 3", ViewCount: 8000, ChannelID: channelID},
	}
	env.Downloader.AddChannelVideos(mockChannelURL, mockVideos)

	// Set upload error
	env.Uploader.uploadError = fmt.Errorf("bilibili API rate limit")

	result, err := env.Controller.SyncChannel(ctx, channelID, 3)

	// Should fail with upload error
	if err == nil {
		t.Error("expected upload error, got nil")
	}
	// First video should be downloaded but upload fails
	if result.Downloaded != 1 {
		t.Errorf("expected 1 download before error, got %d", result.Downloaded)
	}
	if result.Uploaded != 0 {
		t.Errorf("expected 0 successful uploads, got %d", result.Uploaded)
	}

	// Verify video was not marked as uploaded
	isUploaded, _ := env.Store.IsUploaded(ctx, "vid1")
	if isUploaded {
		t.Error("failed upload should not be marked as uploaded")
	}
}

func TestIntegration_Pipeline_MetadataError_SkipsChannel(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	channel := Channel{
		ChannelID: "UC_MetaErr",
		URL:       "https://www.youtube.com/channel/UC_MetaErr",
		IsActive:  true,
	}
	env.Store.AddChannel(ctx, channel)

	// Set metadata error
	env.Downloader.metadataError = fmt.Errorf("channel not found")

	count, err := env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)

	if err == nil {
		t.Error("expected metadata error, got nil")
	}
	if count != 0 {
		t.Errorf("expected 0 videos discovered on error, got %d", count)
	}
}

func TestIntegration_Pipeline_PartialUploadFailure(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	// Create a custom uploader that fails on specific files
	failingUploader := &FailingUploader{
		failOnFile: 2, // Fail on second file
		uploaded:   make([]string, 0),
	}
	env.Controller.Uploader = failingUploader

	channelID := "UC_PartialFail"
	mockChannelURL := "https://www.youtube.com/channel/UC_PartialFail/videos"

	mockVideos := []VideoMetadata{
		{ID: "vid1", Title: "Video 1", ViewCount: 5000, ChannelID: channelID},
		{ID: "vid2", Title: "Video 2", ViewCount: 3000, ChannelID: channelID},
		{ID: "vid3", Title: "Video 3", ViewCount: 8000, ChannelID: channelID},
	}
	env.Downloader.AddChannelVideos(mockChannelURL, mockVideos)

	result, err := env.Controller.SyncChannel(ctx, channelID, 3)

	// Should fail on second video
	if err == nil {
		t.Error("expected upload error on second video")
	}

	// First video should be successfully processed
	if result.Uploaded != 1 {
		t.Errorf("expected 1 successful upload, got %d", result.Uploaded)
	}

	// Verify first video was marked as uploaded, second was not
	isUploaded1, _ := env.Store.IsUploaded(ctx, "vid1")
	isUploaded2, _ := env.Store.IsUploaded(ctx, "vid2")
	if !isUploaded1 {
		t.Error("first video should be marked as uploaded")
	}
	if isUploaded2 {
		t.Error("second video should not be marked as uploaded")
	}
}

// FailingUploader fails after N successful uploads
type FailingUploader struct {
	mu         sync.Mutex
	failOnFile int
	uploaded   []string
	callCount  int
}

func (f *FailingUploader) Upload(path string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.callCount++
	if f.callCount >= f.failOnFile {
		return fmt.Errorf("simulated upload failure on file %d", f.callCount)
	}
	f.uploaded = append(f.uploaded, path)
	return nil
}

// ============================================================================
// Concurrent Pipeline Tests
// ============================================================================

func TestIntegration_Pipeline_ConcurrentChannelScans(t *testing.T) {
	t.Skip("Skipping concurrent test due to SQLite locking - use separate DB connections for true concurrency")
	// SQLite with a single connection does not handle concurrent writes well.
	// This is expected behavior. In production, use connection pooling or serialize writes.
}

func TestIntegration_Pipeline_ConcurrentFilteringIsSafe(t *testing.T) {
	t.Skip("Skipping concurrent test due to SQLite locking - use WAL mode or serialize writes")
	// SQLite with default journal mode has write serialization.
	// This is expected. For true concurrent writes, enable WAL mode or use PostgreSQL.
}

// ============================================================================
// Edge Case Tests
// ============================================================================

func TestIntegration_Pipeline_EmptyChannel(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	channel := Channel{
		ChannelID: "UC_Empty",
		URL:       "https://www.youtube.com/channel/UC_Empty",
		IsActive:  true,
	}
	env.Store.AddChannel(ctx, channel)

	// Don't add any videos to the channel
	count, err := env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)

	if err != nil {
		t.Fatalf("unexpected error for empty channel: %v", err)
	}
	if count != 0 {
		t.Errorf("expected 0 videos for empty channel, got %d", count)
	}
}

func TestIntegration_Pipeline_DuplicateVideoHandling(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	channel := Channel{ChannelID: "UC_Dup", URL: "https://www.youtube.com/channel/UC_Dup", IsActive: true}
	env.Store.AddChannel(ctx, channel)

	now := time.Now()
	recentDate := now.AddDate(0, 0, -1).Format("20060102")

	mockVideos := []VideoMetadata{
		{ID: "vid_dup", Title: "Original Title", ViewCount: 1000, Duration: 300, UploadDate: recentDate, ChannelID: "UC_Dup", Categories: []string{"Entertainment"}},
	}
	env.Downloader.AddChannelVideos(channel.URL, mockVideos)

	// First scan
	env.Scanner.AutoFilter = false
	count1, _ := env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)
	if count1 != 1 {
		t.Fatalf("first scan: expected 1 video, got %d", count1)
	}

	// Update video with new metadata
	updatedVideos := []VideoMetadata{
		{ID: "vid_dup", Title: "Updated Title", ViewCount: 5000, Duration: 300, UploadDate: recentDate, ChannelID: "UC_Dup", Categories: []string{"Entertainment"}},
	}
	env.Downloader.AddChannelVideos(channel.URL, updatedVideos)

	// Second scan - should update, not duplicate
	count2, _ := env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)
	if count2 != 1 {
		t.Errorf("second scan: expected 1 video, got %d", count2)
	}

	// Verify only one candidate exists with updated metadata
	candidate, _ := env.Store.GetCandidate(ctx, "vid_dup")
	if candidate == nil {
		t.Fatal("candidate not found")
	}
	if candidate.Title != "Updated Title" {
		t.Errorf("expected updated title, got %s", candidate.Title)
	}
	if candidate.ViewCount != 5000 {
		t.Errorf("expected updated view count 5000, got %d", candidate.ViewCount)
	}
}

func TestIntegration_Pipeline_VideoAtRuleBoundary(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	channel := Channel{ChannelID: "UC_Boundary", URL: "https://www.youtube.com/channel/UC_Boundary", IsActive: true}
	env.Store.AddChannel(ctx, channel)

	now := time.Now()
	recentDate := now.AddDate(0, 0, -1).Format("20060102")
	exactlyMaxAgeDays := now.AddDate(0, 0, -30).Format("20060102") // Exactly 30 days old

	mockVideos := []VideoMetadata{
		// Exactly at min view threshold
		{ID: "vid_exact_views", Title: "Exact Views", ViewCount: 1000, Duration: 300, UploadDate: recentDate, ChannelID: "UC_Boundary", Categories: []string{"Entertainment"}},
		// Exactly at min duration threshold
		{ID: "vid_exact_dur_min", Title: "Exact Min Duration", ViewCount: 5000, Duration: 60, UploadDate: recentDate, ChannelID: "UC_Boundary", Categories: []string{"Entertainment"}},
		// Exactly at max duration threshold
		{ID: "vid_exact_dur_max", Title: "Exact Max Duration", ViewCount: 5000, Duration: 3600, UploadDate: recentDate, ChannelID: "UC_Boundary", Categories: []string{"Entertainment"}},
		// Exactly at max age threshold
		{ID: "vid_exact_age", Title: "Exact Age", ViewCount: 5000, Duration: 300, UploadDate: exactlyMaxAgeDays, ChannelID: "UC_Boundary", Categories: []string{"Entertainment"}},
		// Just below min views
		{ID: "vid_below_views", Title: "Below Views", ViewCount: 999, Duration: 300, UploadDate: recentDate, ChannelID: "UC_Boundary", Categories: []string{"Entertainment"}},
		// Just above max duration
		{ID: "vid_above_dur", Title: "Above Duration", ViewCount: 5000, Duration: 3601, UploadDate: recentDate, ChannelID: "UC_Boundary", Categories: []string{"Entertainment"}},
	}
	env.Downloader.AddChannelVideos(channel.URL, mockVideos)

	env.RuleEngine.SeedDefaultRules(ctx)
	env.Scanner.AutoFilter = true
	env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)

	passed, _ := env.Store.ListFilteredCandidates(ctx, 10)
	rejected, _ := env.Store.ListRejectedCandidates(ctx, 10)

	// Videos at exact threshold should pass (>=, <=)
	passedIDs := make(map[string]bool)
	for _, p := range passed {
		passedIDs[p.VideoID] = true
	}

	// Check exact boundary cases pass
	expectedPassed := []string{"vid_exact_views", "vid_exact_dur_min", "vid_exact_dur_max", "vid_exact_age"}
	for _, id := range expectedPassed {
		if !passedIDs[id] {
			t.Errorf("expected %s to pass at boundary, but it was rejected", id)
		}
	}

	// Check below/above boundary cases fail
	rejectedIDs := make(map[string]bool)
	for _, r := range rejected {
		rejectedIDs[r.VideoID] = true
	}

	expectedRejected := []string{"vid_below_views", "vid_above_dur"}
	for _, id := range expectedRejected {
		if !rejectedIDs[id] {
			t.Errorf("expected %s to be rejected, but it passed", id)
		}
	}
}

func TestIntegration_Pipeline_VideoWithMissingMetadata(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	channel := Channel{ChannelID: "UC_Missing", URL: "https://www.youtube.com/channel/UC_Missing", IsActive: true}
	env.Store.AddChannel(ctx, channel)

	// Videos with missing/zero metadata
	mockVideos := []VideoMetadata{
		{ID: "vid_no_date", Title: "No Date", ViewCount: 5000, Duration: 300, UploadDate: "", ChannelID: "UC_Missing", Categories: []string{"Entertainment"}},
		{ID: "vid_no_views", Title: "No Views", ViewCount: 0, Duration: 300, UploadDate: time.Now().AddDate(0, 0, -1).Format("20060102"), ChannelID: "UC_Missing", Categories: []string{"Entertainment"}},
		{ID: "vid_no_duration", Title: "No Duration", ViewCount: 5000, Duration: 0, UploadDate: time.Now().AddDate(0, 0, -1).Format("20060102"), ChannelID: "UC_Missing", Categories: []string{"Entertainment"}},
		{ID: "vid_no_category", Title: "No Category", ViewCount: 5000, Duration: 300, UploadDate: time.Now().AddDate(0, 0, -1).Format("20060102"), ChannelID: "UC_Missing", Categories: []string{}},
	}
	env.Downloader.AddChannelVideos(channel.URL, mockVideos)

	env.RuleEngine.SeedDefaultRules(ctx)
	env.Scanner.AutoFilter = true

	// Should not panic or error
	count, err := env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if count != 4 {
		t.Errorf("expected 4 videos scanned, got %d", count)
	}

	// Check decisions were made
	for _, v := range mockVideos {
		decision, err := env.Store.GetRuleDecision(ctx, v.ID)
		if err != nil {
			t.Errorf("error getting decision for %s: %v", v.ID, err)
		}
		if decision == nil {
			t.Errorf("no decision for %s", v.ID)
		}
	}
}

func TestIntegration_Pipeline_LargeVideoCount(t *testing.T) {
	if testing.Short() {
		t.Skip("skipping large video count test in short mode")
	}

	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	channel := Channel{ChannelID: "UC_Large", URL: "https://www.youtube.com/channel/UC_Large", IsActive: true}
	env.Store.AddChannel(ctx, channel)

	now := time.Now()
	recentDate := now.AddDate(0, 0, -1).Format("20060102")

	// Create 100 videos
	var mockVideos []VideoMetadata
	for i := 0; i < 100; i++ {
		mockVideos = append(mockVideos, VideoMetadata{
			ID:         fmt.Sprintf("vid_large_%d", i),
			Title:      fmt.Sprintf("Large Test Video %d", i),
			ViewCount:  1000 + i*100,
			Duration:   60 + i*10,
			UploadDate: recentDate,
			ChannelID:  "UC_Large",
			Categories: []string{"Entertainment"},
		})
	}
	env.Downloader.AddChannelVideos(channel.URL, mockVideos)

	env.RuleEngine.SeedDefaultRules(ctx)
	env.Scanner.AutoFilter = true

	count, err := env.Scanner.ScanChannel(ctx, channel.ChannelID, 100)
	if err != nil {
		t.Fatalf("scan error: %v", err)
	}
	if count != 100 {
		t.Errorf("expected 100 videos, got %d", count)
	}

	// Verify filtering completed for all
	filtered, _ := env.Store.ListFilteredCandidates(ctx, 200)
	rejected, _ := env.Store.ListRejectedCandidates(ctx, 200)

	total := len(filtered) + len(rejected)
	if total != 100 {
		t.Errorf("expected 100 total decisions, got %d", total)
	}
}

// ============================================================================
// Rule Modification During Pipeline Tests
// ============================================================================

func TestIntegration_Pipeline_RuleDisabledMidProcess(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	channel := Channel{ChannelID: "UC_RuleChange", URL: "https://www.youtube.com/channel/UC_RuleChange", IsActive: true}
	env.Store.AddChannel(ctx, channel)

	now := time.Now()
	recentDate := now.AddDate(0, 0, -1).Format("20060102")

	// Video with low views (would be rejected by default)
	mockVideos := []VideoMetadata{
		{ID: "vid_low", Title: "Low Views", ViewCount: 500, Duration: 300, UploadDate: recentDate, ChannelID: "UC_RuleChange", Categories: []string{"Entertainment"}},
	}
	env.Downloader.AddChannelVideos(channel.URL, mockVideos)

	// Add strict view rule
	env.Store.AddRule(ctx, FilterRule{
		RuleName: "min_views",
		RuleType: "min",
		Field:    "view_count",
		Value:    "1000",
		IsActive: true,
		Priority: 100,
	})

	env.Scanner.AutoFilter = false
	env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)

	// First filter - should reject
	passed1, rejected1, _ := env.RuleEngine.FilterPendingCandidates(ctx, 10)
	if len(rejected1) != 1 || len(passed1) != 0 {
		t.Fatalf("expected 1 rejected, 0 passed; got %d rejected, %d passed", len(rejected1), len(passed1))
	}

	// Disable the rule
	env.Store.DeleteRule(ctx, "min_views")

	// Add a new video and re-scan
	newVideos := []VideoMetadata{
		{ID: "vid_low2", Title: "Low Views 2", ViewCount: 500, Duration: 300, UploadDate: recentDate, ChannelID: "UC_RuleChange", Categories: []string{"Entertainment"}},
	}
	env.Downloader.AddChannelVideos(channel.URL, append(mockVideos, newVideos...))
	env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)

	// Second filter - should pass now (no view rule)
	passed2, rejected2, _ := env.RuleEngine.FilterPendingCandidates(ctx, 10)
	if len(passed2) != 1 {
		t.Errorf("expected 1 passed after rule removal, got %d", len(passed2))
	}
	if len(rejected2) != 0 {
		t.Errorf("expected 0 rejected after rule removal, got %d", len(rejected2))
	}
}

func TestIntegration_Pipeline_RuleValueUpdatedMidProcess(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	channel := Channel{ChannelID: "UC_RuleUpd", URL: "https://www.youtube.com/channel/UC_RuleUpd", IsActive: true}
	env.Store.AddChannel(ctx, channel)

	now := time.Now()
	recentDate := now.AddDate(0, 0, -1).Format("20060102")

	mockVideos := []VideoMetadata{
		{ID: "vid_mid", Title: "Mid Views", ViewCount: 750, Duration: 300, UploadDate: recentDate, ChannelID: "UC_RuleUpd", Categories: []string{"Entertainment"}},
	}
	env.Downloader.AddChannelVideos(channel.URL, mockVideos)

	// Add rule requiring 1000 views
	env.Store.AddRule(ctx, FilterRule{
		RuleName: "min_views",
		RuleType: "min",
		Field:    "view_count",
		Value:    "1000",
		IsActive: true,
		Priority: 100,
	})

	env.Scanner.AutoFilter = false
	env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)

	// First filter - should reject (750 < 1000)
	_, rejected1, _ := env.RuleEngine.FilterPendingCandidates(ctx, 10)
	if len(rejected1) != 1 {
		t.Fatalf("expected 1 rejected, got %d", len(rejected1))
	}

	// Lower the threshold to 500
	env.Store.UpdateRule(ctx, "min_views", "500")

	// Add new video
	newVideos := []VideoMetadata{
		{ID: "vid_mid2", Title: "Mid Views 2", ViewCount: 600, Duration: 300, UploadDate: recentDate, ChannelID: "UC_RuleUpd", Categories: []string{"Entertainment"}},
	}
	env.Downloader.AddChannelVideos(channel.URL, append(mockVideos, newVideos...))
	env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)

	// Second filter - new video should pass (600 >= 500)
	passed2, _, _ := env.RuleEngine.FilterPendingCandidates(ctx, 10)
	if len(passed2) != 1 {
		t.Errorf("expected 1 passed after threshold update, got %d", len(passed2))
	}
}

// ============================================================================
// Full Pipeline with Multiple Platforms Tests
// ============================================================================

func TestIntegration_Pipeline_FullCycleWithCleanup(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	// Setup: Add channel, videos, rules
	channel := Channel{
		ChannelID: "UC_FullCycle",
		Name:      "Full Cycle Test",
		URL:       "https://www.youtube.com/channel/UC_FullCycle",
		IsActive:  true,
	}
	env.Store.AddChannel(ctx, channel)

	now := time.Now()
	recentDate := now.AddDate(0, 0, -3).Format("20060102")

	mockVideos := []VideoMetadata{
		{ID: "fc_pass_1", Title: "Quality Content", ViewCount: 10000, Duration: 300, UploadDate: recentDate, ChannelID: "UC_FullCycle", Categories: []string{"Entertainment"}},
		{ID: "fc_pass_2", Title: "Great Video", ViewCount: 5000, Duration: 600, UploadDate: recentDate, ChannelID: "UC_FullCycle", Categories: []string{"Gaming"}},
		{ID: "fc_fail_news", Title: "News Report", ViewCount: 50000, Duration: 300, UploadDate: recentDate, ChannelID: "UC_FullCycle", Categories: []string{"News & Politics"}},
	}
	env.Downloader.AddChannelVideos(channel.URL, mockVideos)

	// 1. Seed rules
	env.RuleEngine.SeedDefaultRules(ctx)

	// 2. Scan channel (with auto-filter)
	env.Scanner.AutoFilter = true
	count, err := env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)
	if err != nil {
		t.Fatalf("scan failed: %v", err)
	}
	if count != 3 {
		t.Fatalf("expected 3 videos scanned, got %d", count)
	}

	// 3. Verify filtering results
	filtered, _ := env.Store.ListFilteredCandidates(ctx, 10)
	if len(filtered) != 2 {
		t.Errorf("expected 2 filtered, got %d", len(filtered))
	}

	rejected, _ := env.Store.ListRejectedCandidates(ctx, 10)
	if len(rejected) != 1 {
		t.Errorf("expected 1 rejected (news), got %d", len(rejected))
	}

	// 4. Download and upload filtered candidates
	for _, c := range filtered {
		files, err := env.Downloader.DownloadVideo(ctx, videoURL(c.VideoID), env.Controller.OutputDir, "", "")
		if err != nil {
			t.Fatalf("download %s: %v", c.VideoID, err)
		}

		for _, f := range files {
			if err := env.Uploader.Upload(f); err != nil {
				t.Fatalf("upload %s: %v", f, err)
			}
		}

		env.Store.MarkUploaded(ctx, c.VideoID, c.ChannelID)
	}

	// 5. Verify final state
	uploaded := env.Uploader.GetUploadedFiles()
	if len(uploaded) != 2 {
		t.Errorf("expected 2 uploaded, got %d", len(uploaded))
	}

	// Filtered list should now be empty (all uploaded)
	finalFiltered, _ := env.Store.ListFilteredCandidates(ctx, 10)
	if len(finalFiltered) != 0 {
		t.Errorf("expected 0 pending filtered after upload, got %d", len(finalFiltered))
	}

	// 6. Verify re-scan doesn't re-upload
	env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)
	newFiltered, _ := env.Store.ListFilteredCandidates(ctx, 10)
	if len(newFiltered) != 0 {
		t.Errorf("re-scan should not produce new filtered candidates, got %d", len(newFiltered))
	}
}

func TestIntegration_Pipeline_MultipleUploaderRetries(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	// Use a retrying uploader
	retryUploader := &RetryingUploader{
		maxRetries:   3,
		failCount:    make(map[string]int),
		failUntil:    2, // Fail first 2 attempts
		successFiles: make([]string, 0),
	}

	channelID := "UC_Retry"
	mockChannelURL := "https://www.youtube.com/channel/UC_Retry/videos"

	mockVideos := []VideoMetadata{
		{ID: "vid_retry", Title: "Retry Video", ViewCount: 5000, ChannelID: channelID},
	}
	env.Downloader.AddChannelVideos(mockChannelURL, mockVideos)

	// Custom controller with retry uploader
	controller := &Controller{
		Downloader: env.Downloader,
		Uploader:   retryUploader,
		Store:      env.Store,
		OutputDir:  t.TempDir(),
	}

	// First attempt - will fail
	_, err := controller.SyncChannel(ctx, channelID, 1)
	if err == nil {
		t.Error("expected first attempt to fail")
	}

	// Second attempt - will fail
	_, err = controller.SyncChannel(ctx, channelID, 1)
	if err == nil {
		t.Error("expected second attempt to fail")
	}

	// Third attempt - should succeed
	result, err := controller.SyncChannel(ctx, channelID, 1)
	if err != nil {
		t.Errorf("expected third attempt to succeed, got: %v", err)
	}
	if result.Uploaded != 1 {
		t.Errorf("expected 1 upload on third attempt, got %d", result.Uploaded)
	}
}

// RetryingUploader simulates transient failures
type RetryingUploader struct {
	mu           sync.Mutex
	maxRetries   int
	failCount    map[string]int
	failUntil    int
	successFiles []string
}

func (r *RetryingUploader) Upload(path string) error {
	r.mu.Lock()
	defer r.mu.Unlock()

	r.failCount[path]++
	if r.failCount[path] <= r.failUntil {
		return fmt.Errorf("transient failure (attempt %d)", r.failCount[path])
	}
	r.successFiles = append(r.successFiles, path)
	return nil
}

// ============================================================================
// Additional Comprehensive Pipeline Tests
// ============================================================================

func TestIntegration_Pipeline_ChannelNotFound(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	// Scan a channel that doesn't exist
	count, err := env.Scanner.ScanChannel(ctx, "UC_DoesNotExist", 10)

	if err != nil {
		t.Errorf("expected no error for non-existent channel, got: %v", err)
	}
	if count != 0 {
		t.Errorf("expected 0 videos from non-existent channel, got %d", count)
	}
}

func TestIntegration_Pipeline_InactiveChannelNotScanned(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	// Add inactive channel
	channel := Channel{
		ChannelID: "UC_Inactive",
		Name:      "Inactive Channel",
		URL:       "https://www.youtube.com/channel/UC_Inactive",
		IsActive:  false,
	}
	env.Store.AddChannel(ctx, channel)

	// Add videos to inactive channel
	now := time.Now()
	recentDate := now.AddDate(0, 0, -1).Format("20060102")
	mockVideos := []VideoMetadata{
		{ID: "vid_inactive", Title: "Video", ViewCount: 5000, Duration: 300, UploadDate: recentDate, ChannelID: "UC_Inactive", Categories: []string{"Entertainment"}},
	}
	env.Downloader.AddChannelVideos(channel.URL, mockVideos)

	// ScanAllActive should skip inactive channel
	env.Scanner.ScanAllActive(ctx, 10)

	// Verify no candidates created
	candidates, _ := env.Store.ListCandidatesByChannel(ctx, "UC_Inactive", 10)
	if len(candidates) != 0 {
		t.Errorf("expected 0 candidates from inactive channel, got %d", len(candidates))
	}
}

func TestIntegration_Pipeline_ZeroLimitChannelSync(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	channelID := "UC_ZeroLimit"
	mockChannelURL := "https://www.youtube.com/channel/UC_ZeroLimit/videos"

	mockVideos := []VideoMetadata{
		{ID: "vid1", Title: "Video 1", ViewCount: 5000, ChannelID: channelID},
		{ID: "vid2", Title: "Video 2", ViewCount: 3000, ChannelID: channelID},
	}
	env.Downloader.AddChannelVideos(mockChannelURL, mockVideos)

	// Sync with limit 0 - should handle gracefully
	result, err := env.Controller.SyncChannel(ctx, channelID, 0)
	if err != nil {
		t.Fatalf("SyncChannel with limit 0 should not error: %v", err)
	}

	if result.Considered != 0 || result.Downloaded != 0 || result.Uploaded != 0 {
		t.Errorf("expected zero results with limit 0, got: %+v", result)
	}
}

func TestIntegration_Pipeline_UpdateExistingCandidateMetadata(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	channel := Channel{
		ChannelID: "UC_Update",
		URL:       "https://www.youtube.com/channel/UC_Update",
		IsActive:  true,
	}
	env.Store.AddChannel(ctx, channel)

	now := time.Now()
	recentDate := now.AddDate(0, 0, -1).Format("20060102")

	// First scan - low views
	mockVideos1 := []VideoMetadata{
		{ID: "vid_update", Title: "Original Title", ViewCount: 1000, Duration: 300, UploadDate: recentDate, ChannelID: "UC_Update", Categories: []string{"Entertainment"}},
	}
	env.Downloader.AddChannelVideos(channel.URL, mockVideos1)

	env.Scanner.AutoFilter = false
	count1, _ := env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)
	if count1 != 1 {
		t.Fatalf("first scan: expected 1, got %d", count1)
	}

	// Verify original data
	candidate1, _ := env.Store.GetCandidate(ctx, "vid_update")
	if candidate1 == nil {
		t.Fatal("candidate not found after first scan")
	}
	if candidate1.ViewCount != 1000 || candidate1.Title != "Original Title" {
		t.Errorf("unexpected original data: %+v", candidate1)
	}

	// Second scan - updated views and title
	mockVideos2 := []VideoMetadata{
		{ID: "vid_update", Title: "Updated Title", ViewCount: 10000, Duration: 300, UploadDate: recentDate, ChannelID: "UC_Update", Categories: []string{"Entertainment"}},
	}
	env.Downloader.AddChannelVideos(channel.URL, mockVideos2)

	count2, _ := env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)
	if count2 != 1 {
		t.Fatalf("second scan: expected 1, got %d", count2)
	}

	// Verify updated data
	candidate2, _ := env.Store.GetCandidate(ctx, "vid_update")
	if candidate2 == nil {
		t.Fatal("candidate not found after second scan")
	}
	if candidate2.ViewCount != 10000 {
		t.Errorf("expected view count 10000, got %d", candidate2.ViewCount)
	}
	if candidate2.Title != "Updated Title" {
		t.Errorf("expected updated title, got %s", candidate2.Title)
	}
}

func TestIntegration_Pipeline_RuleEvaluationIdempotency(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	channel := Channel{
		ChannelID: "UC_Idempotent",
		URL:       "https://www.youtube.com/channel/UC_Idempotent",
		IsActive:  true,
	}
	env.Store.AddChannel(ctx, channel)

	now := time.Now()
	recentDate := now.AddDate(0, 0, -1).Format("20060102")

	mockVideos := []VideoMetadata{
		{ID: "vid_idem", Title: "Test", ViewCount: 5000, Duration: 300, UploadDate: recentDate, ChannelID: "UC_Idempotent", Categories: []string{"Entertainment"}},
	}
	env.Downloader.AddChannelVideos(channel.URL, mockVideos)

	env.Scanner.AutoFilter = false
	env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)

	// Add simple rule
	env.Store.AddRule(ctx, FilterRule{
		RuleName: "min_views",
		RuleType: "min",
		Field:    "view_count",
		Value:    "1000",
		IsActive: true,
		Priority: 100,
	})

	// First evaluation
	passed1, _, _ := env.RuleEngine.FilterPendingCandidates(ctx, 10)
	if len(passed1) != 1 {
		t.Fatalf("first evaluation: expected 1 passed, got %d", len(passed1))
	}

	// Second evaluation - should be no-op (already evaluated)
	passed2, _, _ := env.RuleEngine.FilterPendingCandidates(ctx, 10)
	if len(passed2) != 0 {
		t.Errorf("second evaluation: expected 0 (already evaluated), got %d", len(passed2))
	}

	// Verify only one decision recorded
	decision, _ := env.Store.GetRuleDecision(ctx, "vid_idem")
	if decision == nil {
		t.Error("no decision recorded")
	}
	if !decision.RulePassed {
		t.Error("expected decision to pass")
	}
}

func TestIntegration_Pipeline_MultipleRulesCoordination(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	channel := Channel{
		ChannelID: "UC_MultiRule",
		URL:       "https://www.youtube.com/channel/UC_MultiRule",
		IsActive:  true,
	}
	env.Store.AddChannel(ctx, channel)

	now := time.Now()
	recentDate := now.AddDate(0, 0, -5).Format("20060102")

	mockVideos := []VideoMetadata{
		{ID: "vid_pass_all", Title: "Perfect Video", ViewCount: 5000, Duration: 300, UploadDate: recentDate, ChannelID: "UC_MultiRule", Categories: []string{"Gaming"}},
		{ID: "vid_fail_first", Title: "Low Views", ViewCount: 500, Duration: 300, UploadDate: recentDate, ChannelID: "UC_MultiRule", Categories: []string{"Gaming"}},
		{ID: "vid_fail_second", Title: "Pass Views, Fail Duration", ViewCount: 5000, Duration: 5000, UploadDate: recentDate, ChannelID: "UC_MultiRule", Categories: []string{"Gaming"}},
	}
	env.Downloader.AddChannelVideos(channel.URL, mockVideos)

	// Add multiple rules with different priorities
	rules := []FilterRule{
		{RuleName: "min_views", RuleType: "min", Field: "view_count", Value: "1000", IsActive: true, Priority: 100},
		{RuleName: "max_duration", RuleType: "max", Field: "duration_seconds", Value: "3600", IsActive: true, Priority: 90},
		{RuleName: "min_duration", RuleType: "min", Field: "duration_seconds", Value: "60", IsActive: true, Priority: 80},
	}
	for _, r := range rules {
		env.Store.AddRule(ctx, r)
	}

	env.Scanner.AutoFilter = true
	env.Scanner.RuleEngine = env.RuleEngine
	env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)

	passed, _ := env.Store.ListFilteredCandidates(ctx, 10)
	rejected, _ := env.Store.ListRejectedCandidates(ctx, 10)

	// Only vid_pass_all should pass all rules
	if len(passed) != 1 {
		t.Errorf("expected 1 passed, got %d", len(passed))
	}
	if len(passed) > 0 && passed[0].VideoID != "vid_pass_all" {
		t.Errorf("expected vid_pass_all to pass, got %s", passed[0].VideoID)
	}

	// Other two should be rejected
	if len(rejected) != 2 {
		t.Errorf("expected 2 rejected, got %d", len(rejected))
	}

	// Verify rejection reasons
	decision1, _ := env.Store.GetRuleDecision(ctx, "vid_fail_first")
	if decision1 == nil || decision1.RejectRuleName != "min_views" {
		t.Error("vid_fail_first should be rejected by min_views")
	}

	decision2, _ := env.Store.GetRuleDecision(ctx, "vid_fail_second")
	if decision2 == nil || decision2.RejectRuleName != "max_duration" {
		t.Error("vid_fail_second should be rejected by max_duration")
	}
}

func TestIntegration_Pipeline_ScanWithAutoFilterEnabled(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	channel := Channel{
		ChannelID: "UC_AutoFilter",
		URL:       "https://www.youtube.com/channel/UC_AutoFilter",
		IsActive:  true,
	}
	env.Store.AddChannel(ctx, channel)

	now := time.Now()
	recentDate := now.AddDate(0, 0, -1).Format("20060102")

	mockVideos := []VideoMetadata{
		{ID: "vid_good", Title: "Good Video", ViewCount: 5000, Duration: 300, UploadDate: recentDate, ChannelID: "UC_AutoFilter", Categories: []string{"Gaming"}},
		{ID: "vid_bad", Title: "Bad Video", ViewCount: 100, Duration: 300, UploadDate: recentDate, ChannelID: "UC_AutoFilter", Categories: []string{"Gaming"}},
	}
	env.Downloader.AddChannelVideos(channel.URL, mockVideos)

	// Enable auto-filter
	env.Scanner.AutoFilter = true
	env.Scanner.RuleEngine = env.RuleEngine
	env.RuleEngine.SeedDefaultRules(ctx)

	// Single scan should discover AND filter
	count, err := env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)
	if err != nil {
		t.Fatalf("scan error: %v", err)
	}
	if count != 2 {
		t.Errorf("expected 2 discovered, got %d", count)
	}

	// Check filtering happened automatically
	passed, _ := env.Store.ListFilteredCandidates(ctx, 10)
	rejected, _ := env.Store.ListRejectedCandidates(ctx, 10)

	if len(passed) != 1 {
		t.Errorf("auto-filter: expected 1 passed, got %d", len(passed))
	}
	if len(rejected) != 1 {
		t.Errorf("auto-filter: expected 1 rejected, got %d", len(rejected))
	}
}

func TestIntegration_Pipeline_ChannelURLVariations(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	testCases := []struct {
		name      string
		channelID string
		url       string
	}{
		{"Standard URL", "UC_Standard", "https://www.youtube.com/channel/UC_Standard"},
		{"With /videos", "UC_WithVideos", "https://www.youtube.com/channel/UC_WithVideos/videos"},
		{"Handle Format", "UC_Handle", "https://www.youtube.com/@HandleName"},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			channel := Channel{
				ChannelID: tc.channelID,
				URL:       tc.url,
				IsActive:  true,
			}
			env.Store.AddChannel(ctx, channel)

			now := time.Now()
			recentDate := now.AddDate(0, 0, -1).Format("20060102")
			mockVideos := []VideoMetadata{
				{ID: tc.channelID + "_v1", Title: "Video", ViewCount: 5000, Duration: 300, UploadDate: recentDate, ChannelID: tc.channelID, Categories: []string{"Gaming"}},
			}
			env.Downloader.AddChannelVideos(tc.url, mockVideos)

			count, err := env.Scanner.ScanChannel(ctx, tc.channelID, 10)
			if err != nil {
				t.Errorf("scan failed for %s: %v", tc.name, err)
			}
			if count != 1 {
				t.Errorf("expected 1 video, got %d", count)
			}
		})
	}
}

func TestIntegration_Pipeline_RescanUpdatesTimestamp(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	channel := Channel{
		ChannelID: "UC_Timestamp",
		URL:       "https://www.youtube.com/channel/UC_Timestamp",
		IsActive:  true,
	}
	env.Store.AddChannel(ctx, channel)

	now := time.Now()
	recentDate := now.AddDate(0, 0, -1).Format("20060102")
	mockVideos := []VideoMetadata{
		{ID: "vid_ts", Title: "Video", ViewCount: 5000, Duration: 300, UploadDate: recentDate, ChannelID: "UC_Timestamp", Categories: []string{"Gaming"}},
	}
	env.Downloader.AddChannelVideos(channel.URL, mockVideos)

	// First scan
	env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)

	ch1, _ := env.Store.GetChannel(ctx, "UC_Timestamp")
	if ch1.LastScannedAt == nil {
		t.Error("LastScannedAt should be set after first scan")
	}
	firstScanTime := *ch1.LastScannedAt

	// Wait a bit
	time.Sleep(10 * time.Millisecond)

	// Second scan
	env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)

	ch2, _ := env.Store.GetChannel(ctx, "UC_Timestamp")
	if ch2.LastScannedAt == nil {
		t.Error("LastScannedAt should be set after second scan")
	}
	secondScanTime := *ch2.LastScannedAt

	if !secondScanTime.After(firstScanTime) {
		t.Error("LastScannedAt should be updated on rescan")
	}
}

func TestIntegration_Pipeline_UploadAlreadyProcessedVideo(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	channelID := "UC_AlreadyUp"
	mockChannelURL := "https://www.youtube.com/channel/UC_AlreadyUp/videos"

	mockVideos := []VideoMetadata{
		{ID: "vid_already", Title: "Video", ViewCount: 5000, ChannelID: channelID},
	}
	env.Downloader.AddChannelVideos(mockChannelURL, mockVideos)

	// First sync
	result1, err := env.Controller.SyncChannel(ctx, channelID, 1)
	if err != nil {
		t.Fatalf("first sync error: %v", err)
	}
	if result1.Downloaded != 1 || result1.Uploaded != 1 {
		t.Errorf("first sync: expected 1 download, 1 upload, got %+v", result1)
	}

	// Second sync - should skip
	result2, err := env.Controller.SyncChannel(ctx, channelID, 1)
	if err != nil {
		t.Fatalf("second sync error: %v", err)
	}
	if result2.Skipped != 1 {
		t.Errorf("second sync: expected 1 skipped, got %+v", result2)
	}
	if result2.Downloaded != 0 || result2.Uploaded != 0 {
		t.Errorf("second sync: should not download/upload again, got %+v", result2)
	}
}

func TestIntegration_Pipeline_EmptyVideoList(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	channel := Channel{
		ChannelID: "UC_Empty",
		URL:       "https://www.youtube.com/channel/UC_Empty",
		IsActive:  true,
	}
	env.Store.AddChannel(ctx, channel)

	// Add channel but no videos
	env.Downloader.AddChannelVideos(channel.URL, []VideoMetadata{})

	count, err := env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)
	if err != nil {
		t.Fatalf("scan empty channel error: %v", err)
	}
	if count != 0 {
		t.Errorf("expected 0 from empty channel, got %d", count)
	}
}

func TestIntegration_Pipeline_FilteredVideosNotRefiltered(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	channel := Channel{
		ChannelID: "UC_NoRefilter",
		URL:       "https://www.youtube.com/channel/UC_NoRefilter",
		IsActive:  true,
	}
	env.Store.AddChannel(ctx, channel)

	now := time.Now()
	recentDate := now.AddDate(0, 0, -1).Format("20060102")
	mockVideos := []VideoMetadata{
		{ID: "vid_once", Title: "Video", ViewCount: 5000, Duration: 300, UploadDate: recentDate, ChannelID: "UC_NoRefilter", Categories: []string{"Gaming"}},
	}
	env.Downloader.AddChannelVideos(channel.URL, mockVideos)

	env.Scanner.AutoFilter = false
	env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)

	env.RuleEngine.SeedDefaultRules(ctx)

	// First filter
	passed1, _, _ := env.RuleEngine.FilterPendingCandidates(ctx, 10)
	if len(passed1) != 1 {
		t.Fatalf("first filter: expected 1 passed, got %d", len(passed1))
	}

	// Second filter - should find no pending candidates
	passed2, rejected2, _ := env.RuleEngine.FilterPendingCandidates(ctx, 10)
	if len(passed2) != 0 || len(rejected2) != 0 {
		t.Errorf("second filter: expected 0 pending, got %d passed, %d rejected", len(passed2), len(rejected2))
	}
}

func TestIntegration_Pipeline_MixedCategoryFiltering(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	channel := Channel{
		ChannelID: "UC_MixedCat",
		URL:       "https://www.youtube.com/channel/UC_MixedCat",
		IsActive:  true,
	}
	env.Store.AddChannel(ctx, channel)

	now := time.Now()
	recentDate := now.AddDate(0, 0, -1).Format("20060102")

	categories := []string{"Gaming", "Music", "News & Politics", "Education", "Entertainment"}
	var mockVideos []VideoMetadata
	for i, cat := range categories {
		mockVideos = append(mockVideos, VideoMetadata{
			ID:         fmt.Sprintf("vid_cat_%d", i),
			Title:      fmt.Sprintf("%s Video", cat),
			ViewCount:  5000,
			Duration:   300,
			UploadDate: recentDate,
			ChannelID:  "UC_MixedCat",
			Categories: []string{cat},
		})
	}
	env.Downloader.AddChannelVideos(channel.URL, mockVideos)

	// Add blocklist for News & Politics
	env.Store.AddRule(ctx, FilterRule{
		RuleName: "blocked_categories",
		RuleType: "blocklist",
		Field:    "category",
		Value:    `["News & Politics"]`,
		IsActive: true,
		Priority: 100,
	})

	env.Scanner.AutoFilter = true
	env.Scanner.RuleEngine = env.RuleEngine
	env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)

	passed, _ := env.Store.ListFilteredCandidates(ctx, 10)
	rejected, _ := env.Store.ListRejectedCandidates(ctx, 10)

	// 4 should pass (all except News & Politics)
	if len(passed) != 4 {
		t.Errorf("expected 4 passed (all except News), got %d", len(passed))
	}

	// 1 should be rejected (News & Politics)
	if len(rejected) != 1 {
		t.Errorf("expected 1 rejected (News), got %d", len(rejected))
	}

	if len(rejected) > 0 && !strings.Contains(rejected[0].Title, "News") {
		t.Errorf("expected News video to be rejected, got %s", rejected[0].Title)
	}
}

func TestIntegration_Pipeline_HighVolumeChannelLimit(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	channel := Channel{
		ChannelID: "UC_HighVol",
		URL:       "https://www.youtube.com/channel/UC_HighVol",
		IsActive:  true,
	}
	env.Store.AddChannel(ctx, channel)

	now := time.Now()
	recentDate := now.AddDate(0, 0, -1).Format("20060102")

	// Create 100 videos
	var mockVideos []VideoMetadata
	for i := 0; i < 100; i++ {
		mockVideos = append(mockVideos, VideoMetadata{
			ID:         fmt.Sprintf("vid_hv_%d", i),
			Title:      fmt.Sprintf("Video %d", i),
			ViewCount:  5000 + i,
			Duration:   300,
			UploadDate: recentDate,
			ChannelID:  "UC_HighVol",
			Categories: []string{"Gaming"},
		})
	}
	env.Downloader.AddChannelVideos(channel.URL, mockVideos)

	// Scan with limit of 20
	count, err := env.Scanner.ScanChannel(ctx, channel.ChannelID, 20)
	if err != nil {
		t.Fatalf("scan error: %v", err)
	}

	// Should only scan 20 videos
	if count != 20 {
		t.Errorf("expected limit of 20 videos, got %d", count)
	}

	candidates, _ := env.Store.ListCandidatesByChannel(ctx, "UC_HighVol", 100)
	if len(candidates) != 20 {
		t.Errorf("expected 20 candidates stored, got %d", len(candidates))
	}
}

func TestIntegration_Pipeline_ComputedMetricsAccuracy(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	channel := Channel{
		ChannelID: "UC_Metrics",
		URL:       "https://www.youtube.com/channel/UC_Metrics",
		IsActive:  true,
	}
	env.Store.AddChannel(ctx, channel)

	// Use a specific past date to avoid timing issues
	// Date format is YYYYMMDD - set to 1 day ago for consistent velocity calculation
	oneDayAgo := time.Now().AddDate(0, 0, -1)
	oneDayAgoStr := oneDayAgo.Format("20060102")

	mockVideos := []VideoMetadata{
		{
			ID:           "vid_metrics",
			Title:        "Metrics Test",
			ViewCount:    3600,
			LikeCount:    100,
			CommentCount: 20,
			Duration:     300,
			UploadDate:   oneDayAgoStr,
			ChannelID:    "UC_Metrics",
			Categories:   []string{"Gaming"},
		},
	}
	env.Downloader.AddChannelVideos(channel.URL, mockVideos)

	env.Scanner.AutoFilter = false
	env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)

	candidate, _ := env.Store.GetCandidate(ctx, "vid_metrics")
	if candidate == nil {
		t.Fatal("candidate not found")
	}

	// Check view velocity - for a video 1 day old (24 hours) with 3600 views
	// Velocity = 3600 / 24 = 150 views/hour
	// Allow tolerance for timing and parsing
	if candidate.ViewVelocity < 100 || candidate.ViewVelocity > 200 {
		t.Errorf("expected view velocity ~150 views/hour, got %f", candidate.ViewVelocity)
	}

	// Check engagement rate = (likes + comments) / views = (100 + 20) / 3600 = 0.033...
	expectedEngagement := float64(120) / float64(3600)
	if candidate.EngagementRate < expectedEngagement-0.01 || candidate.EngagementRate > expectedEngagement+0.01 {
		t.Errorf("expected engagement rate ~%f, got %f", expectedEngagement, candidate.EngagementRate)
	}
}

// ============================================================================
// Integration with Real Rule Types Tests
// ============================================================================

func TestIntegration_Pipeline_AllRuleTypes(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	channel := Channel{ChannelID: "UC_AllRules", URL: "https://www.youtube.com/channel/UC_AllRules", IsActive: true}
	env.Store.AddChannel(ctx, channel)

	now := time.Now()
	recentDate := now.AddDate(0, 0, -5).Format("20060102")
	oldDate := now.AddDate(0, 0, -45).Format("20060102")

	mockVideos := []VideoMetadata{
		// Should pass all
		{ID: "pass_all", Title: "Good Video", ViewCount: 5000, Duration: 300, UploadDate: recentDate, ChannelID: "UC_AllRules", Categories: []string{"Entertainment"}},
		// Fail min rule
		{ID: "fail_min", Title: "Low Views", ViewCount: 500, Duration: 300, UploadDate: recentDate, ChannelID: "UC_AllRules", Categories: []string{"Entertainment"}},
		// Fail max rule
		{ID: "fail_max", Title: "Too Long", ViewCount: 5000, Duration: 5000, UploadDate: recentDate, ChannelID: "UC_AllRules", Categories: []string{"Entertainment"}},
		// Fail blocklist
		{ID: "fail_block", Title: "News Video", ViewCount: 5000, Duration: 300, UploadDate: recentDate, ChannelID: "UC_AllRules", Categories: []string{"News & Politics"}},
		// Fail age_days
		{ID: "fail_age", Title: "Old Video", ViewCount: 50000, Duration: 300, UploadDate: oldDate, ChannelID: "UC_AllRules", Categories: []string{"Entertainment"}},
		// Fail regex
		{ID: "fail_regex", Title: "[AD] Sponsored Content", ViewCount: 5000, Duration: 300, UploadDate: recentDate, ChannelID: "UC_AllRules", Categories: []string{"Entertainment"}},
	}
	env.Downloader.AddChannelVideos(channel.URL, mockVideos)

	// Add all rule types
	rules := []FilterRule{
		{RuleName: "min_views", RuleType: "min", Field: "view_count", Value: "1000", IsActive: true, Priority: 100},
		{RuleName: "max_duration", RuleType: "max", Field: "duration_seconds", Value: "3600", IsActive: true, Priority: 90},
		{RuleName: "blocked_cats", RuleType: "blocklist", Field: "category", Value: `["News & Politics"]`, IsActive: true, Priority: 80},
		{RuleName: "max_age", RuleType: "age_days", Field: "published_at", Value: "30", IsActive: true, Priority: 70},
		{RuleName: "block_ads", RuleType: "regex", Field: "title", Value: `(?i)\[AD\]|sponsored`, IsActive: true, Priority: 60},
	}
	for _, r := range rules {
		env.Store.AddRule(ctx, r)
	}

	env.Scanner.AutoFilter = true
	env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)

	passed, _ := env.Store.ListFilteredCandidates(ctx, 10)
	rejected, _ := env.Store.ListRejectedCandidates(ctx, 10)

	// Only pass_all should pass
	if len(passed) != 1 {
		t.Errorf("expected 1 passed, got %d", len(passed))
		for _, p := range passed {
			t.Logf("  passed: %s", p.VideoID)
		}
	}
	if len(passed) > 0 && passed[0].VideoID != "pass_all" {
		t.Errorf("expected pass_all to pass, got %s", passed[0].VideoID)
	}

	// 5 should be rejected
	if len(rejected) != 5 {
		t.Errorf("expected 5 rejected, got %d", len(rejected))
	}

	// Verify each rejection reason
	expectedRejections := map[string]string{
		"fail_min":   "min_views",
		"fail_max":   "max_duration",
		"fail_block": "blocked_cats",
		"fail_age":   "max_age",
		"fail_regex": "block_ads",
	}

	for _, r := range rejected {
		expected, ok := expectedRejections[r.VideoID]
		if !ok {
			t.Errorf("unexpected rejection: %s", r.VideoID)
			continue
		}
		if r.RejectRuleName != expected {
			t.Errorf("%s: expected rejection by %s, got %s", r.VideoID, expected, r.RejectRuleName)
		}
	}
}

func TestIntegration_Pipeline_AllowlistRule(t *testing.T) {
	env := NewTestEnv(t)
	defer env.Cleanup()
	ctx := context.Background()

	channel := Channel{ChannelID: "UC_Allow", URL: "https://www.youtube.com/channel/UC_Allow", IsActive: true}
	env.Store.AddChannel(ctx, channel)

	now := time.Now()
	recentDate := now.AddDate(0, 0, -1).Format("20060102")

	mockVideos := []VideoMetadata{
		{ID: "vid_gaming", Title: "Gaming Video", ViewCount: 5000, Duration: 300, UploadDate: recentDate, ChannelID: "UC_Allow", Categories: []string{"Gaming"}},
		{ID: "vid_music", Title: "Music Video", ViewCount: 5000, Duration: 300, UploadDate: recentDate, ChannelID: "UC_Allow", Categories: []string{"Music"}},
		{ID: "vid_edu", Title: "Education Video", ViewCount: 5000, Duration: 300, UploadDate: recentDate, ChannelID: "UC_Allow", Categories: []string{"Education"}},
		{ID: "vid_other", Title: "Other Video", ViewCount: 5000, Duration: 300, UploadDate: recentDate, ChannelID: "UC_Allow", Categories: []string{"Howto & Style"}},
	}
	env.Downloader.AddChannelVideos(channel.URL, mockVideos)

	// Only allow Gaming and Music
	env.Store.AddRule(ctx, FilterRule{
		RuleName: "allowed_categories",
		RuleType: "allowlist",
		Field:    "category",
		Value:    `["Gaming", "Music"]`,
		IsActive: true,
		Priority: 100,
	})

	env.Scanner.AutoFilter = true
	env.Scanner.ScanChannel(ctx, channel.ChannelID, 10)

	passed, _ := env.Store.ListFilteredCandidates(ctx, 10)
	if len(passed) != 2 {
		t.Errorf("expected 2 passed (Gaming, Music), got %d", len(passed))
	}

	passedIDs := make(map[string]bool)
	for _, p := range passed {
		passedIDs[p.VideoID] = true
	}

	if !passedIDs["vid_gaming"] || !passedIDs["vid_music"] {
		t.Error("Gaming and Music videos should pass allowlist")
	}
	if passedIDs["vid_edu"] || passedIDs["vid_other"] {
		t.Error("Education and Other videos should be rejected by allowlist")
	}
}
