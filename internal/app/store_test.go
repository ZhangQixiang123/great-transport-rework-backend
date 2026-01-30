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
