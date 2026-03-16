package app

import (
	"context"
	"os"
	"testing"
	"time"
)

func TestSQLiteStoreChannels(t *testing.T) {
	dbPath := t.TempDir() + "/test.db"
	store, err := NewSQLiteStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(dbPath)

	ctx := context.Background()
	if err := store.EnsureSchema(ctx); err != nil {
		t.Fatal(err)
	}

	// Test AddChannel
	ch := Channel{
		ChannelID:          "UC123",
		Name:               "Test Channel",
		URL:                "https://www.youtube.com/channel/UC123",
		ScanFrequencyHours: 6,
		IsActive:           true,
	}
	if err := store.AddChannel(ctx, ch); err != nil {
		t.Fatalf("AddChannel: %v", err)
	}

	// Test GetChannel
	got, err := store.GetChannel(ctx, "UC123")
	if err != nil {
		t.Fatalf("GetChannel: %v", err)
	}
	if got == nil {
		t.Fatal("GetChannel returned nil")
	}
	if got.Name != "Test Channel" {
		t.Fatalf("got name %q, want %q", got.Name, "Test Channel")
	}
	if !got.IsActive {
		t.Fatal("channel should be active")
	}

	// Test ListActiveChannels
	channels, err := store.ListActiveChannels(ctx)
	if err != nil {
		t.Fatalf("ListActiveChannels: %v", err)
	}
	if len(channels) != 1 {
		t.Fatalf("got %d channels, want 1", len(channels))
	}

	// Test UpdateChannelScanned
	if err := store.UpdateChannelScanned(ctx, "UC123"); err != nil {
		t.Fatalf("UpdateChannelScanned: %v", err)
	}
	got, _ = store.GetChannel(ctx, "UC123")
	if got.LastScannedAt == nil {
		t.Fatal("LastScannedAt should be set")
	}

	// Test DeactivateChannel
	if err := store.DeactivateChannel(ctx, "UC123"); err != nil {
		t.Fatalf("DeactivateChannel: %v", err)
	}
	channels, _ = store.ListActiveChannels(ctx)
	if len(channels) != 0 {
		t.Fatalf("got %d active channels, want 0", len(channels))
	}
}

func TestSQLiteStoreVideoCandidates(t *testing.T) {
	dbPath := t.TempDir() + "/test.db"
	store, err := NewSQLiteStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(dbPath)

	ctx := context.Background()
	if err := store.EnsureSchema(ctx); err != nil {
		t.Fatal(err)
	}

	// Add a channel first
	ch := Channel{
		ChannelID: "UC123",
		URL:       "https://www.youtube.com/channel/UC123",
		IsActive:  true,
	}
	store.AddChannel(ctx, ch)

	// Test UpsertCandidate
	now := time.Now().UTC()
	vc := VideoCandidate{
		VideoID:         "vid123",
		ChannelID:       "UC123",
		Title:           "Test Video",
		Description:     "A test video description",
		DurationSeconds: 120,
		ViewCount:       1000,
		LikeCount:       50,
		CommentCount:    10,
		PublishedAt:     &now,
		Tags:            []string{"tag1", "tag2"},
		Category:        "Gaming",
		ViewVelocity:    100.5,
		EngagementRate:  0.06,
	}
	if err := store.UpsertCandidate(ctx, vc); err != nil {
		t.Fatalf("UpsertCandidate: %v", err)
	}

	// Test GetCandidate
	got, err := store.GetCandidate(ctx, "vid123")
	if err != nil {
		t.Fatalf("GetCandidate: %v", err)
	}
	if got == nil {
		t.Fatal("GetCandidate returned nil")
	}
	if got.Title != "Test Video" {
		t.Fatalf("got title %q, want %q", got.Title, "Test Video")
	}
	if got.ViewCount != 1000 {
		t.Fatalf("got view count %d, want 1000", got.ViewCount)
	}
	if len(got.Tags) != 2 {
		t.Fatalf("got %d tags, want 2", len(got.Tags))
	}

	// Test ListCandidatesByChannel
	candidates, err := store.ListCandidatesByChannel(ctx, "UC123", 10)
	if err != nil {
		t.Fatalf("ListCandidatesByChannel: %v", err)
	}
	if len(candidates) != 1 {
		t.Fatalf("got %d candidates, want 1", len(candidates))
	}

	// Test ListPendingCandidates
	candidates, err = store.ListPendingCandidates(ctx, 10)
	if err != nil {
		t.Fatalf("ListPendingCandidates: %v", err)
	}
	if len(candidates) != 1 {
		t.Fatalf("got %d pending candidates, want 1", len(candidates))
	}

	// Mark as uploaded and verify it's no longer pending
	store.MarkUploaded(ctx, "vid123", "UC123")
	candidates, _ = store.ListPendingCandidates(ctx, 10)
	if len(candidates) != 0 {
		t.Fatalf("got %d pending candidates after upload, want 0", len(candidates))
	}

	// Test UpdateCandidateMetrics
	if err := store.UpdateCandidateMetrics(ctx, "vid123", 2000, 100, 20); err != nil {
		t.Fatalf("UpdateCandidateMetrics: %v", err)
	}
	got, _ = store.GetCandidate(ctx, "vid123")
	if got.ViewCount != 2000 {
		t.Fatalf("got view count %d after update, want 2000", got.ViewCount)
	}
}

func TestGetChannelNotFound(t *testing.T) {
	dbPath := t.TempDir() + "/test.db"
	store, err := NewSQLiteStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(dbPath)

	ctx := context.Background()
	store.EnsureSchema(ctx)

	got, err := store.GetChannel(ctx, "nonexistent")
	if err != nil {
		t.Fatalf("GetChannel: %v", err)
	}
	if got != nil {
		t.Fatal("expected nil for nonexistent channel")
	}
}

func TestMarkUploadedWithBvid_SavesBvidCorrectly(t *testing.T) {
	dbPath := t.TempDir() + "/test.db"
	store, err := NewSQLiteStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(dbPath)

	ctx := context.Background()
	if err := store.EnsureSchema(ctx); err != nil {
		t.Fatal(err)
	}

	// Add a channel first
	ch := Channel{ChannelID: "UC123", URL: "https://youtube.com/c/UC123", IsActive: true}
	store.AddChannel(ctx, ch)

	// Add a candidate
	vc := VideoCandidate{VideoID: "vid123", ChannelID: "UC123", Title: "Test"}
	store.UpsertCandidate(ctx, vc)

	// Mark uploaded with bvid
	bvid := "BV1AB411c7XY"
	if err := store.MarkUploadedWithBvid(ctx, "vid123", "UC123", bvid); err != nil {
		t.Fatalf("MarkUploadedWithBvid: %v", err)
	}

	// Verify the bvid was saved
	upload, err := store.GetUpload(ctx, "vid123")
	if err != nil {
		t.Fatalf("GetUpload: %v", err)
	}
	if upload == nil {
		t.Fatal("GetUpload returned nil")
	}
	if upload.BilibiliBvid != bvid {
		t.Fatalf("got bvid %q, want %q", upload.BilibiliBvid, bvid)
	}
}

func TestSaveUploadPerformance_AllMetrics(t *testing.T) {
	dbPath := t.TempDir() + "/test.db"
	store, err := NewSQLiteStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(dbPath)

	ctx := context.Background()
	if err := store.EnsureSchema(ctx); err != nil {
		t.Fatal(err)
	}

	// Setup: add channel, candidate, and upload
	ch := Channel{ChannelID: "UC123", URL: "https://youtube.com/c/UC123", IsActive: true}
	store.AddChannel(ctx, ch)
	vc := VideoCandidate{VideoID: "vid123", ChannelID: "UC123", Title: "Test"}
	store.UpsertCandidate(ctx, vc)
	store.MarkUploadedWithBvid(ctx, "vid123", "UC123", "BV123")

	// Save performance with all metrics
	perf := UploadPerformance{
		UploadID:        "vid123",
		CheckpointHours: 24,
		RecordedAt:      time.Now().UTC(),
		Views:           10000,
		Likes:           500,
		Coins:           200,
		Favorites:       150,
		Shares:          50,
		Danmaku:         300,
		Comments:        100,
		ViewVelocity:    416.67,
		EngagementRate:  0.085,
	}
	if err := store.SaveUploadPerformance(ctx, perf); err != nil {
		t.Fatalf("SaveUploadPerformance: %v", err)
	}

	// Retrieve and verify
	perfs, err := store.GetUploadPerformance(ctx, "vid123")
	if err != nil {
		t.Fatalf("GetUploadPerformance: %v", err)
	}
	if len(perfs) != 1 {
		t.Fatalf("got %d performance records, want 1", len(perfs))
	}
	p := perfs[0]
	if p.Views != 10000 {
		t.Errorf("Views: got %d, want 10000", p.Views)
	}
	if p.Likes != 500 {
		t.Errorf("Likes: got %d, want 500", p.Likes)
	}
	if p.Coins != 200 {
		t.Errorf("Coins: got %d, want 200", p.Coins)
	}
	if p.Favorites != 150 {
		t.Errorf("Favorites: got %d, want 150", p.Favorites)
	}
	if p.Shares != 50 {
		t.Errorf("Shares: got %d, want 50", p.Shares)
	}
	if p.Danmaku != 300 {
		t.Errorf("Danmaku: got %d, want 300", p.Danmaku)
	}
	if p.Comments != 100 {
		t.Errorf("Comments: got %d, want 100", p.Comments)
	}
	if p.CheckpointHours != 24 {
		t.Errorf("CheckpointHours: got %d, want 24", p.CheckpointHours)
	}
}

func TestGetUploadsForTracking_FiltersByCheckpoint(t *testing.T) {
	dbPath := t.TempDir() + "/test.db"
	store, err := NewSQLiteStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(dbPath)

	ctx := context.Background()
	if err := store.EnsureSchema(ctx); err != nil {
		t.Fatal(err)
	}

	// Setup channel
	ch := Channel{ChannelID: "UC123", URL: "https://youtube.com/c/UC123", IsActive: true}
	store.AddChannel(ctx, ch)

	// Create uploads with different ages
	// Upload 1: 2 hours ago (should be eligible for 1h checkpoint)
	vc1 := VideoCandidate{VideoID: "vid1", ChannelID: "UC123", Title: "Recent Video"}
	store.UpsertCandidate(ctx, vc1)
	store.MarkUploadedWithBvid(ctx, "vid1", "UC123", "BV001")

	// Upload 2: already has 1h checkpoint recorded
	vc2 := VideoCandidate{VideoID: "vid2", ChannelID: "UC123", Title: "Already Tracked"}
	store.UpsertCandidate(ctx, vc2)
	store.MarkUploadedWithBvid(ctx, "vid2", "UC123", "BV002")
	store.SaveUploadPerformance(ctx, UploadPerformance{
		UploadID:        "vid2",
		CheckpointHours: 1,
		RecordedAt:      time.Now().UTC(),
		Views:           100,
	})

	// Get uploads for 1h checkpoint - vid2 should be excluded (already has it)
	uploads, err := store.GetUploadsForTracking(ctx, 1)
	if err != nil {
		t.Fatalf("GetUploadsForTracking: %v", err)
	}

	// Check that vid2 is not in the results (it already has 1h checkpoint)
	for _, u := range uploads {
		if u.VideoID == "vid2" {
			t.Error("vid2 should not be returned - it already has 1h checkpoint")
		}
	}
}

func TestSaveUploadOutcome_AllLabels(t *testing.T) {
	dbPath := t.TempDir() + "/test.db"
	store, err := NewSQLiteStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(dbPath)

	ctx := context.Background()
	if err := store.EnsureSchema(ctx); err != nil {
		t.Fatal(err)
	}

	// Setup
	ch := Channel{ChannelID: "UC123", URL: "https://youtube.com/c/UC123", IsActive: true}
	store.AddChannel(ctx, ch)

	labels := []string{"viral", "successful", "standard", "failed"}
	for i, label := range labels {
		videoID := "vid" + string(rune('a'+i))
		vc := VideoCandidate{VideoID: videoID, ChannelID: "UC123", Title: "Test " + label}
		store.UpsertCandidate(ctx, vc)
		store.MarkUploadedWithBvid(ctx, videoID, "UC123", "BV00"+string(rune('1'+i)))

		outcome := UploadOutcome{
			UploadID:            videoID,
			Label:               label,
			LabeledAt:           time.Now().UTC(),
			FinalViews:          (i + 1) * 100000,
			FinalEngagementRate: float64(i+1) * 0.01,
			FinalCoins:          (i + 1) * 1000,
		}
		if err := store.SaveUploadOutcome(ctx, outcome); err != nil {
			t.Fatalf("SaveUploadOutcome for %s: %v", label, err)
		}

		// Verify
		got, err := store.GetUploadOutcome(ctx, videoID)
		if err != nil {
			t.Fatalf("GetUploadOutcome for %s: %v", label, err)
		}
		if got == nil {
			t.Fatalf("GetUploadOutcome returned nil for %s", label)
		}
		if got.Label != label {
			t.Errorf("Label: got %q, want %q", got.Label, label)
		}
	}
}

func TestGetUploadStats_ReturnsCorrectAggregates(t *testing.T) {
	dbPath := t.TempDir() + "/test.db"
	store, err := NewSQLiteStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(dbPath)

	ctx := context.Background()
	if err := store.EnsureSchema(ctx); err != nil {
		t.Fatal(err)
	}

	// Setup channel
	ch := Channel{ChannelID: "UC123", URL: "https://youtube.com/c/UC123", IsActive: true}
	store.AddChannel(ctx, ch)

	// Create 3 uploads with different states
	// Upload 1: has bvid, performance, and outcome (viral)
	vc1 := VideoCandidate{VideoID: "vid1", ChannelID: "UC123", Title: "Viral Video"}
	store.UpsertCandidate(ctx, vc1)
	store.MarkUploadedWithBvid(ctx, "vid1", "UC123", "BV001")
	store.SaveUploadPerformance(ctx, UploadPerformance{
		UploadID: "vid1", CheckpointHours: 24, RecordedAt: time.Now().UTC(),
		Views: 1000000, Likes: 50000, Coins: 20000, EngagementRate: 0.07,
	})
	store.SaveUploadOutcome(ctx, UploadOutcome{
		UploadID: "vid1", Label: "viral", LabeledAt: time.Now().UTC(),
	})

	// Upload 2: has bvid, performance, and outcome (standard)
	vc2 := VideoCandidate{VideoID: "vid2", ChannelID: "UC123", Title: "Standard Video"}
	store.UpsertCandidate(ctx, vc2)
	store.MarkUploadedWithBvid(ctx, "vid2", "UC123", "BV002")
	store.SaveUploadPerformance(ctx, UploadPerformance{
		UploadID: "vid2", CheckpointHours: 24, RecordedAt: time.Now().UTC(),
		Views: 20000, Likes: 500, Coins: 100, EngagementRate: 0.025,
	})
	store.SaveUploadOutcome(ctx, UploadOutcome{
		UploadID: "vid2", Label: "standard", LabeledAt: time.Now().UTC(),
	})

	// Upload 3: no bvid (old upload)
	vc3 := VideoCandidate{VideoID: "vid3", ChannelID: "UC123", Title: "Old Upload"}
	store.UpsertCandidate(ctx, vc3)
	store.MarkUploaded(ctx, "vid3", "UC123")

	// Get stats
	stats, err := store.GetUploadStats(ctx)
	if err != nil {
		t.Fatalf("GetUploadStats: %v", err)
	}
	if stats == nil {
		t.Fatal("GetUploadStats returned nil")
	}

	// Verify aggregates
	if stats.TotalUploads != 3 {
		t.Errorf("TotalUploads: got %d, want 3", stats.TotalUploads)
	}
	if stats.UploadsWithBvid != 2 {
		t.Errorf("UploadsWithBvid: got %d, want 2", stats.UploadsWithBvid)
	}
	if stats.UploadsWithPerformance != 2 {
		t.Errorf("UploadsWithPerformance: got %d, want 2", stats.UploadsWithPerformance)
	}
	if stats.UploadsByLabel["viral"] != 1 {
		t.Errorf("UploadsByLabel[viral]: got %d, want 1", stats.UploadsByLabel["viral"])
	}
	if stats.UploadsByLabel["standard"] != 1 {
		t.Errorf("UploadsByLabel[standard]: got %d, want 1", stats.UploadsByLabel["standard"])
	}
}

func TestListRecentUploadsWithPerformance(t *testing.T) {
	dbPath := t.TempDir() + "/test.db"
	store, err := NewSQLiteStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(dbPath)

	ctx := context.Background()
	if err := store.EnsureSchema(ctx); err != nil {
		t.Fatal(err)
	}

	// Setup channel
	ch := Channel{ChannelID: "UC123", URL: "https://youtube.com/c/UC123", IsActive: true}
	store.AddChannel(ctx, ch)

	// Create uploads with performance data
	for i := 0; i < 5; i++ {
		videoID := "vid" + string(rune('a'+i))
		vc := VideoCandidate{VideoID: videoID, ChannelID: "UC123", Title: "Video " + string(rune('A'+i))}
		store.UpsertCandidate(ctx, vc)
		store.MarkUploadedWithBvid(ctx, videoID, "UC123", "BV00"+string(rune('1'+i)))
		store.SaveUploadPerformance(ctx, UploadPerformance{
			UploadID:        videoID,
			CheckpointHours: 24,
			RecordedAt:      time.Now().UTC(),
			Views:           (i + 1) * 10000,
			Likes:           (i + 1) * 500,
			Coins:           (i + 1) * 100,
			EngagementRate:  float64(i+1) * 0.01,
		})
	}

	// Get recent uploads with limit
	recent, err := store.ListRecentUploadsWithPerformance(ctx, 3)
	if err != nil {
		t.Fatalf("ListRecentUploadsWithPerformance: %v", err)
	}
	if len(recent) != 3 {
		t.Fatalf("got %d results, want 3", len(recent))
	}

	// Verify each has performance data
	for _, r := range recent {
		if r.BilibiliBvid == "" {
			t.Errorf("Upload %s missing BilibiliBvid", r.VideoID)
		}
		if r.Views == 0 {
			t.Errorf("Upload %s missing Views", r.VideoID)
		}
	}
}

// Phase 3B: Competitor Monitoring Tests

func TestCompetitorChannels(t *testing.T) {
	dbPath := t.TempDir() + "/test.db"
	store, err := NewSQLiteStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(dbPath)

	ctx := context.Background()
	if err := store.EnsureSchema(ctx); err != nil {
		t.Fatal(err)
	}

	// Test AddCompetitorChannel
	ch := CompetitorChannel{
		BilibiliUID:   "12345",
		Name:          "Test Transporter",
		Description:   "A test competitor channel",
		FollowerCount: 100000,
		VideoCount:    50,
		AddedAt:       time.Now().UTC(),
		IsActive:      true,
	}
	if err := store.AddCompetitorChannel(ctx, ch); err != nil {
		t.Fatalf("AddCompetitorChannel: %v", err)
	}

	// Test GetCompetitorChannel
	got, err := store.GetCompetitorChannel(ctx, "12345")
	if err != nil {
		t.Fatalf("GetCompetitorChannel: %v", err)
	}
	if got == nil {
		t.Fatal("GetCompetitorChannel returned nil")
	}
	if got.Name != "Test Transporter" {
		t.Fatalf("got name %q, want %q", got.Name, "Test Transporter")
	}
	if got.FollowerCount != 100000 {
		t.Fatalf("got follower count %d, want 100000", got.FollowerCount)
	}

	// Test ListCompetitorChannels
	channels, err := store.ListCompetitorChannels(ctx)
	if err != nil {
		t.Fatalf("ListCompetitorChannels: %v", err)
	}
	if len(channels) != 1 {
		t.Fatalf("got %d channels, want 1", len(channels))
	}

	// Test DeactivateCompetitorChannel
	if err := store.DeactivateCompetitorChannel(ctx, "12345"); err != nil {
		t.Fatalf("DeactivateCompetitorChannel: %v", err)
	}
	channels, _ = store.ListCompetitorChannels(ctx)
	if len(channels) != 0 {
		t.Fatalf("got %d active channels after deactivation, want 0", len(channels))
	}
}

func TestCompetitorVideos(t *testing.T) {
	dbPath := t.TempDir() + "/test.db"
	store, err := NewSQLiteStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(dbPath)

	ctx := context.Background()
	if err := store.EnsureSchema(ctx); err != nil {
		t.Fatal(err)
	}

	// Add a competitor channel first
	ch := CompetitorChannel{
		BilibiliUID: "12345",
		Name:        "Test Channel",
		IsActive:    true,
		AddedAt:     time.Now().UTC(),
	}
	store.AddCompetitorChannel(ctx, ch)

	// Test ListCompetitorVideos - empty initially
	videos, err := store.ListCompetitorVideos(ctx, "", "", 10)
	if err != nil {
		t.Fatalf("ListCompetitorVideos: %v", err)
	}
	if len(videos) != 0 {
		t.Fatalf("got %d videos, want 0", len(videos))
	}
}

func TestGetCompetitorStats(t *testing.T) {
	dbPath := t.TempDir() + "/test.db"
	store, err := NewSQLiteStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(dbPath)

	ctx := context.Background()
	if err := store.EnsureSchema(ctx); err != nil {
		t.Fatal(err)
	}

	// Add competitor channels
	for i := 0; i < 3; i++ {
		ch := CompetitorChannel{
			BilibiliUID: string(rune('a' + i)),
			Name:        "Channel " + string(rune('A'+i)),
			IsActive:    i < 2, // First 2 active, last one inactive
			AddedAt:     time.Now().UTC(),
		}
		store.AddCompetitorChannel(ctx, ch)
		if i == 2 {
			store.DeactivateCompetitorChannel(ctx, ch.BilibiliUID)
		}
	}

	// Get stats
	stats, err := store.GetCompetitorStats(ctx)
	if err != nil {
		t.Fatalf("GetCompetitorStats: %v", err)
	}
	if stats == nil {
		t.Fatal("GetCompetitorStats returned nil")
	}
	if stats.TotalChannels != 3 {
		t.Errorf("TotalChannels: got %d, want 3", stats.TotalChannels)
	}
	if stats.ActiveChannels != 2 {
		t.Errorf("ActiveChannels: got %d, want 2", stats.ActiveChannels)
	}
}

func TestGetTrainingDataSummary(t *testing.T) {
	dbPath := t.TempDir() + "/test.db"
	store, err := NewSQLiteStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(dbPath)

	ctx := context.Background()
	if err := store.EnsureSchema(ctx); err != nil {
		t.Fatal(err)
	}

	// Get empty summary
	summary, err := store.GetTrainingDataSummary(ctx)
	if err != nil {
		t.Fatalf("GetTrainingDataSummary: %v", err)
	}
	if summary == nil {
		t.Fatal("GetTrainingDataSummary returned nil")
	}
	if summary.Total != 0 {
		t.Errorf("Total: got %d, want 0", summary.Total)
	}
}

func TestUploadJobCRUD(t *testing.T) {
	f, err := os.CreateTemp("", "test-upload-job-*.db")
	if err != nil {
		t.Fatal(err)
	}
	dbPath := f.Name()
	f.Close()
	store, err := NewSQLiteStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(dbPath)

	ctx := context.Background()
	if err := store.EnsureSchema(ctx); err != nil {
		t.Fatal(err)
	}

	// Create a job
	jobID, err := store.CreateUploadJob(ctx, "dQw4w9WgXcQ", "Test Title", "Test description\n本视频搬运自YouTube", "搬运,音乐")
	if err != nil {
		t.Fatalf("CreateUploadJob: %v", err)
	}
	if jobID <= 0 {
		t.Fatalf("expected positive job ID, got %d", jobID)
	}

	// Get the job
	job, err := store.GetUploadJob(ctx, jobID)
	if err != nil {
		t.Fatalf("GetUploadJob: %v", err)
	}
	if job == nil {
		t.Fatal("GetUploadJob returned nil")
	}
	if job.VideoID != "dQw4w9WgXcQ" {
		t.Errorf("VideoID: got %q, want %q", job.VideoID, "dQw4w9WgXcQ")
	}
	if job.Status != "pending" {
		t.Errorf("Status: got %q, want %q", job.Status, "pending")
	}
	if job.Title != "Test Title" {
		t.Errorf("Title: got %q, want %q", job.Title, "Test Title")
	}
	if job.Tags != "搬运,音乐" {
		t.Errorf("Tags: got %q, want %q", job.Tags, "搬运,音乐")
	}

	// Update status to downloading
	if err := store.UpdateUploadJobStatus(ctx, jobID, "downloading", "", ""); err != nil {
		t.Fatalf("UpdateUploadJobStatus (downloading): %v", err)
	}

	job, _ = store.GetUploadJob(ctx, jobID)
	if job.Status != "downloading" {
		t.Errorf("Status after update: got %q, want %q", job.Status, "downloading")
	}

	// Update to completed with bvid
	if err := store.UpdateUploadJobStatus(ctx, jobID, "completed", "BV1AB411c7XY", ""); err != nil {
		t.Fatalf("UpdateUploadJobStatus (completed): %v", err)
	}

	job, _ = store.GetUploadJob(ctx, jobID)
	if job.Status != "completed" {
		t.Errorf("Status: got %q, want %q", job.Status, "completed")
	}
	if job.BilibiliBvid != "BV1AB411c7XY" {
		t.Errorf("BilibiliBvid: got %q, want %q", job.BilibiliBvid, "BV1AB411c7XY")
	}

	// Get nonexistent job
	nilJob, err := store.GetUploadJob(ctx, 99999)
	if err != nil {
		t.Fatalf("GetUploadJob (nonexistent): %v", err)
	}
	if nilJob != nil {
		t.Errorf("expected nil for nonexistent job, got %+v", nilJob)
	}
}

func TestGetNextPendingJob(t *testing.T) {
	f, err := os.CreateTemp("", "test-pending-job-*.db")
	if err != nil {
		t.Fatal(err)
	}
	dbPath := f.Name()
	f.Close()
	store, err := NewSQLiteStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(dbPath)

	ctx := context.Background()
	if err := store.EnsureSchema(ctx); err != nil {
		t.Fatal(err)
	}

	// No jobs yet
	job, err := store.GetNextPendingJob(ctx)
	if err != nil {
		t.Fatalf("GetNextPendingJob (empty): %v", err)
	}
	if job != nil {
		t.Fatal("expected nil when no jobs exist")
	}

	// Create 3 jobs
	id1, _ := store.CreateUploadJob(ctx, "vid1", "Title 1", "Desc 1", "")
	id2, _ := store.CreateUploadJob(ctx, "vid2", "Title 2", "Desc 2", "")
	store.CreateUploadJob(ctx, "vid3", "Title 3", "Desc 3", "")

	// Should get first (lowest ID)
	job, err = store.GetNextPendingJob(ctx)
	if err != nil {
		t.Fatalf("GetNextPendingJob: %v", err)
	}
	if job == nil {
		t.Fatal("expected a job")
	}
	if job.ID != id1 {
		t.Errorf("expected job ID %d, got %d", id1, job.ID)
	}

	// Mark first as completed, second as downloading
	store.UpdateUploadJobStatus(ctx, id1, "completed", "", "")
	store.UpdateUploadJobStatus(ctx, id2, "downloading", "", "")

	// Next pending should be job 3
	job, err = store.GetNextPendingJob(ctx)
	if err != nil {
		t.Fatalf("GetNextPendingJob: %v", err)
	}
	if job == nil {
		t.Fatal("expected a job")
	}
	if job.VideoID != "vid3" {
		t.Errorf("expected vid3, got %s", job.VideoID)
	}
}

func TestListRecentUploadJobs(t *testing.T) {
	f, err := os.CreateTemp("", "test-list-jobs-*.db")
	if err != nil {
		t.Fatal(err)
	}
	dbPath := f.Name()
	f.Close()
	store, err := NewSQLiteStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(dbPath)

	ctx := context.Background()
	if err := store.EnsureSchema(ctx); err != nil {
		t.Fatal(err)
	}

	// Empty list
	jobs, err := store.ListRecentUploadJobs(ctx, 10)
	if err != nil {
		t.Fatalf("ListRecentUploadJobs (empty): %v", err)
	}
	if len(jobs) != 0 {
		t.Fatalf("expected 0 jobs, got %d", len(jobs))
	}

	// Create 5 jobs
	for i := 0; i < 5; i++ {
		store.CreateUploadJob(ctx, "vid"+string(rune('A'+i)), "Title", "Desc", "")
	}

	// List with limit 3 — should get 3 most recent (highest IDs first)
	jobs, err = store.ListRecentUploadJobs(ctx, 3)
	if err != nil {
		t.Fatalf("ListRecentUploadJobs: %v", err)
	}
	if len(jobs) != 3 {
		t.Fatalf("expected 3 jobs, got %d", len(jobs))
	}
	// First job should have the highest ID
	if jobs[0].ID < jobs[1].ID {
		t.Error("jobs should be ordered by ID DESC")
	}

	// List all
	jobs, err = store.ListRecentUploadJobs(ctx, 50)
	if err != nil {
		t.Fatalf("ListRecentUploadJobs (all): %v", err)
	}
	if len(jobs) != 5 {
		t.Fatalf("expected 5 jobs, got %d", len(jobs))
	}
}

func TestUploadJobFailed(t *testing.T) {
	f, err := os.CreateTemp("", "test-upload-job-failed-*.db")
	if err != nil {
		t.Fatal(err)
	}
	dbPath := f.Name()
	f.Close()
	store, err := NewSQLiteStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(dbPath)

	ctx := context.Background()
	if err := store.EnsureSchema(ctx); err != nil {
		t.Fatal(err)
	}

	jobID, _ := store.CreateUploadJob(ctx, "test123", "Title", "Desc", "")

	// Mark as failed with error
	if err := store.UpdateUploadJobStatus(ctx, jobID, "failed", "", "download timeout"); err != nil {
		t.Fatalf("UpdateUploadJobStatus (failed): %v", err)
	}

	job, _ := store.GetUploadJob(ctx, jobID)
	if job.Status != "failed" {
		t.Errorf("Status: got %q, want %q", job.Status, "failed")
	}
	if job.ErrorMessage != "download timeout" {
		t.Errorf("ErrorMessage: got %q, want %q", job.ErrorMessage, "download timeout")
	}
	if job.BilibiliBvid != "" {
		t.Errorf("BilibiliBvid should be empty, got %q", job.BilibiliBvid)
	}
}
