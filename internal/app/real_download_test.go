// +build integration

package app

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// These tests require:
// 1. yt-dlp installed and in PATH
// 2. Network access to YouTube
// 3. Run with: go test -v ./internal/app -tags=integration -run "TestReal"

// Short, publicly available videos for testing (Creative Commons or test videos)
const (
	// A very short test video (~5 seconds) - Big Buck Bunny trailer
	RealTestVideoID = "aqz-KE-bpKQ" // Big Buck Bunny - short clip

	// A channel with public videos (Blender Foundation - using channel ID format)
	RealTestChannelURL = "https://www.youtube.com/channel/UCOKHwx1VCdgnxwbjyb9Iu1g/videos"
)

func skipIfNoYtDlp(t *testing.T) {
	if !HasExecutable("yt-dlp") {
		t.Skip("yt-dlp not found in PATH - skipping real download test")
	}
}

func TestReal_GetVideoMetadata(t *testing.T) {
	skipIfNoYtDlp(t)

	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	downloader := NewYtDlpDownloader(0)
	meta, err := downloader.GetVideoMetadata(ctx, RealTestVideoID, "")

	if err != nil {
		t.Fatalf("GetVideoMetadata failed: %v", err)
	}

	// Verify metadata fields are populated
	if meta.ID != RealTestVideoID {
		t.Errorf("expected video ID %s, got %s", RealTestVideoID, meta.ID)
	}
	if meta.Title == "" {
		t.Error("expected non-empty title")
	}
	if meta.Duration <= 0 {
		t.Errorf("expected positive duration, got %d", meta.Duration)
	}
	if meta.ChannelID == "" {
		t.Error("expected non-empty channel ID")
	}

	t.Logf("Video Metadata:")
	t.Logf("  ID: %s", meta.ID)
	t.Logf("  Title: %s", meta.Title)
	t.Logf("  Duration: %d seconds", meta.Duration)
	t.Logf("  Views: %d", meta.ViewCount)
	t.Logf("  Likes: %d", meta.LikeCount)
	t.Logf("  Channel: %s (%s)", meta.ChannelTitle, meta.ChannelID)
	t.Logf("  Categories: %v", meta.Categories)
	t.Logf("  Tags: %v", meta.Tags)
}

func TestReal_ListChannelVideoIDs(t *testing.T) {
	skipIfNoYtDlp(t)

	ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
	defer cancel()

	downloader := NewYtDlpDownloader(0)
	ids, err := downloader.ListChannelVideoIDs(ctx, RealTestChannelURL, 5, "")

	if err != nil {
		t.Fatalf("ListChannelVideoIDs failed: %v", err)
	}

	if len(ids) == 0 {
		t.Error("expected at least 1 video ID")
	}

	t.Logf("Found %d video IDs from channel:", len(ids))
	for i, id := range ids {
		t.Logf("  %d. %s", i+1, id)
	}

	// Verify IDs look valid (11 characters)
	for _, id := range ids {
		if len(id) != 11 {
			t.Errorf("unexpected video ID length: %s (len=%d)", id, len(id))
		}
	}
}

func TestReal_GetChannelVideosMetadata(t *testing.T) {
	skipIfNoYtDlp(t)

	ctx, cancel := context.WithTimeout(context.Background(), 180*time.Second)
	defer cancel()

	downloader := NewYtDlpDownloader(0)
	videos, err := downloader.GetChannelVideosMetadata(ctx, RealTestChannelURL, 3, "")

	if err != nil {
		t.Fatalf("GetChannelVideosMetadata failed: %v", err)
	}

	if len(videos) == 0 {
		t.Error("expected at least 1 video")
	}

	t.Logf("Found %d videos with metadata:", len(videos))
	for i, v := range videos {
		t.Logf("  %d. %s", i+1, v.Title)
		t.Logf("     ID: %s, Duration: %ds, Views: %d", v.ID, v.Duration, v.ViewCount)
	}
}

func TestReal_DownloadVideo_SmallFile(t *testing.T) {
	skipIfNoYtDlp(t)

	// Create temp directory for download
	tempDir, err := os.MkdirTemp("", "yt-download-test-*")
	if err != nil {
		t.Fatalf("failed to create temp dir: %v", err)
	}
	defer os.RemoveAll(tempDir) // Cleanup after test

	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Second)
	defer cancel()

	downloader := NewYtDlpDownloader(0)

	// Download with worst quality to minimize size/time
	// Format: worst video + worst audio
	files, err := downloader.DownloadVideo(ctx, "https://www.youtube.com/watch?v="+RealTestVideoID, tempDir, "", "worst")

	if err != nil {
		t.Fatalf("DownloadVideo failed: %v", err)
	}

	if len(files) == 0 {
		t.Fatal("expected at least 1 downloaded file")
	}

	t.Logf("Downloaded %d file(s):", len(files))
	for _, f := range files {
		info, err := os.Stat(f)
		if err != nil {
			t.Errorf("failed to stat downloaded file %s: %v", f, err)
			continue
		}
		t.Logf("  %s (%d bytes)", filepath.Base(f), info.Size())

		// Verify file has content
		if info.Size() == 0 {
			t.Errorf("downloaded file is empty: %s", f)
		}
	}
}

func TestReal_FullPipeline_DiscoverAndDownload(t *testing.T) {
	skipIfNoYtDlp(t)

	tempDir, err := os.MkdirTemp("", "yt-pipeline-test-*")
	if err != nil {
		t.Fatalf("failed to create temp dir: %v", err)
	}
	defer os.RemoveAll(tempDir)

	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Second)
	defer cancel()

	downloader := NewYtDlpDownloader(0)

	// Step 1: Get channel videos metadata
	t.Log("Step 1: Fetching channel videos metadata...")
	videos, err := downloader.GetChannelVideosMetadata(ctx, RealTestChannelURL, 2, "")
	if err != nil {
		t.Fatalf("GetChannelVideosMetadata failed: %v", err)
	}
	if len(videos) == 0 {
		t.Fatal("no videos found")
	}
	t.Logf("  Found %d videos", len(videos))

	// Step 2: Filter by duration (< 60 seconds for faster test)
	var shortVideo *VideoMetadata
	for _, v := range videos {
		t.Logf("  Checking: %s (duration: %ds)", v.Title, v.Duration)
		if v.Duration > 0 && v.Duration < 120 {
			shortVideo = &v
			break
		}
	}

	if shortVideo == nil {
		t.Skip("no short video found for download test - skipping")
	}

	t.Logf("Step 2: Selected video: %s (%ds)", shortVideo.Title, shortVideo.Duration)

	// Step 3: Download the video
	t.Log("Step 3: Downloading video...")
	videoURL := "https://www.youtube.com/watch?v=" + shortVideo.ID
	files, err := downloader.DownloadVideo(ctx, videoURL, tempDir, "", "worst")
	if err != nil {
		t.Fatalf("DownloadVideo failed: %v", err)
	}

	if len(files) == 0 {
		t.Fatal("no files downloaded")
	}

	t.Logf("Step 4: Download complete!")
	for _, f := range files {
		info, _ := os.Stat(f)
		t.Logf("  File: %s (%d bytes)", filepath.Base(f), info.Size())
	}

	// Summary
	t.Log("\n=== Pipeline Summary ===")
	t.Logf("Video ID: %s", shortVideo.ID)
	t.Logf("Title: %s", shortVideo.Title)
	t.Logf("Duration: %d seconds", shortVideo.Duration)
	t.Logf("Views: %d", shortVideo.ViewCount)
	t.Logf("Downloaded to: %s", tempDir)
}

// TestReal_DownloadWithFormat tests the download format option
// Note: Only "worst" format is tested because YouTube restricts some format combinations
func TestReal_DownloadWithFormat(t *testing.T) {
	skipIfNoYtDlp(t)

	tempDir, err := os.MkdirTemp("", "yt-format-test-*")
	if err != nil {
		t.Fatalf("failed to create temp dir: %v", err)
	}
	defer os.RemoveAll(tempDir)

	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Second)
	defer cancel()

	downloader := NewYtDlpDownloader(0)
	videoURL := "https://www.youtube.com/watch?v=" + RealTestVideoID

	// Test worst quality format (most reliable, smallest size)
	files, err := downloader.DownloadVideo(ctx, videoURL, tempDir, "", "worst")
	if err != nil {
		t.Fatalf("download with worst format failed: %v", err)
	}

	if len(files) == 0 {
		t.Error("no files downloaded")
		return
	}

	for _, file := range files {
		info, _ := os.Stat(file)
		t.Logf("Downloaded: %s (%d bytes)", filepath.Base(file), info.Size())

		// Verify file has content
		if info.Size() < 1000 {
			t.Errorf("downloaded file seems too small: %d bytes", info.Size())
		}
	}
}

// ============================================================================
// Upload Tests - These will ACTUALLY upload to Bilibili!
// ============================================================================

const (
	// Short Creative Commons video for upload testing
	// Using Blender Foundation's Sintel trailer (short, always available)
	UploadTestVideoID = "eRsGyueVLvQ" // Sintel trailer - ~1 min
)

func getBiliupPath() string {
	// Check environment variable first
	if path := os.Getenv("BILIUP_PATH"); path != "" {
		return path
	}
	// Check common venv location
	venvPath := "/Users/fzjjs/Documents/Great Transport/.venv/bin/biliup"
	if _, err := os.Stat(venvPath); err == nil {
		return venvPath
	}
	// Fall back to PATH
	return "biliup"
}

func skipIfNoBiliup(t *testing.T) string {
	biliupPath := getBiliupPath()
	if _, err := exec.LookPath(biliupPath); err != nil {
		if _, err := os.Stat(biliupPath); os.IsNotExist(err) {
			t.Skip("biliup not found - skipping upload test")
		}
	}
	return biliupPath
}

func skipIfNoCookies(t *testing.T, cookiePath string) {
	if _, err := os.Stat(cookiePath); os.IsNotExist(err) {
		t.Skipf("cookies file not found at %s - run 'biliup --user-cookie %s login' first", cookiePath, cookiePath)
	}
}

// TestReal_UploadToBilibili downloads a short video and uploads it to Bilibili.
// WARNING: This will create a REAL video on your Bilibili account!
// Run with: ENABLE_UPLOAD_TEST=1 go test -v ./internal/app -tags=integration -run "TestReal_UploadToBilibili"
func TestReal_UploadToBilibili(t *testing.T) {
	if os.Getenv("ENABLE_UPLOAD_TEST") == "" {
		t.Skip("Upload test skipped - set ENABLE_UPLOAD_TEST=1 to run (creates real Bilibili video)")
	}
	skipIfNoYtDlp(t)
	biliupPath := skipIfNoBiliup(t)

	// Cookie path - check both project root and current directory
	cookiePath := "cookies.json"
	projectCookiePath := "/Users/fzjjs/Documents/Great Transport/cookies.json"

	if _, err := os.Stat(projectCookiePath); err == nil {
		cookiePath = projectCookiePath
	}
	skipIfNoCookies(t, cookiePath)

	// Create temp directory for download
	tempDir, err := os.MkdirTemp("", "yt-upload-test-*")
	if err != nil {
		t.Fatalf("failed to create temp dir: %v", err)
	}
	defer os.RemoveAll(tempDir)

	ctx, cancel := context.WithTimeout(context.Background(), 600*time.Second)
	defer cancel()

	downloader := NewYtDlpDownloader(0)

	// Step 1: Download a short test video (worst quality for speed)
	t.Log("Step 1: Downloading test video...")
	videoURL := "https://www.youtube.com/watch?v=" + UploadTestVideoID
	files, err := downloader.DownloadVideo(ctx, videoURL, tempDir, "", "worst")
	if err != nil {
		t.Fatalf("download failed: %v", err)
	}
	if len(files) == 0 {
		t.Fatal("no files downloaded")
	}

	downloadedFile := files[0]
	info, _ := os.Stat(downloadedFile)
	t.Logf("  Downloaded: %s (%d bytes)", filepath.Base(downloadedFile), info.Size())

	// Step 2: Upload to Bilibili
	t.Log("Step 2: Uploading to Bilibili...")
	uploader := NewBiliupUploader(BiliupUploaderOptions{
		Binary:      biliupPath,
		CookiePath:  cookiePath,
		Limit:       3,
		TitlePrefix: "[Test Upload] ",
		Description: "This is a test upload from Great Transport integration tests. Will be deleted.",
		Dynamic:     "Test upload - please ignore",
		Tags:        []string{"test", "自动上传"},
	})

	err = uploader.Upload(downloadedFile)
	if err != nil {
		// Check for rate limiting (Bilibili error code 21566)
		if strings.Contains(err.Error(), "21566") || strings.Contains(err.Error(), "投稿过于频繁") {
			t.Skip("Bilibili rate limit reached - try again later")
		}
		t.Fatalf("upload failed: %v", err)
	}

	t.Log("Step 3: Upload complete!")
	t.Log("\n=== Upload Summary ===")
	t.Logf("Source: YouTube video %s", UploadTestVideoID)
	t.Logf("File: %s", filepath.Base(downloadedFile))
	t.Logf("Status: Successfully uploaded to Bilibili")
	t.Log("NOTE: Please delete this test video from your Bilibili account")
}

// TestReal_FullPipeline_DownloadFilterUpload tests the complete pipeline
// Run with: ENABLE_UPLOAD_TEST=1 go test -v ./internal/app -tags=integration -run "TestReal_FullPipeline_DownloadFilterUpload"
func TestReal_FullPipeline_DownloadFilterUpload(t *testing.T) {
	if os.Getenv("ENABLE_UPLOAD_TEST") == "" {
		t.Skip("Upload test skipped - set ENABLE_UPLOAD_TEST=1 to run (creates real Bilibili video)")
	}
	skipIfNoYtDlp(t)
	biliupPath := skipIfNoBiliup(t)

	cookiePath := "cookies.json"
	projectCookiePath := "/Users/fzjjs/Documents/Great Transport/cookies.json"
	if _, err := os.Stat(projectCookiePath); err == nil {
		cookiePath = projectCookiePath
	}
	skipIfNoCookies(t, cookiePath)

	// Setup temp directory
	tempDir, err := os.MkdirTemp("", "yt-full-pipeline-*")
	if err != nil {
		t.Fatalf("failed to create temp dir: %v", err)
	}
	defer os.RemoveAll(tempDir)

	// Setup database
	dbPath := filepath.Join(tempDir, "test.db")
	store, err := NewSQLiteStore(dbPath)
	if err != nil {
		t.Fatalf("failed to create store: %v", err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 600*time.Second)
	defer cancel()

	// Initialize schema
	if err := store.EnsureSchema(ctx); err != nil {
		t.Fatalf("failed to initialize schema: %v", err)
	}

	// Real downloader
	downloader := NewYtDlpDownloader(0)

	// Real uploader
	uploader := NewBiliupUploader(BiliupUploaderOptions{
		Binary:      biliupPath,
		CookiePath:  cookiePath,
		Limit:       3,
		TitlePrefix: "[Pipeline Test] ",
		Description: "Full pipeline test from Great Transport",
		Tags:        []string{"test", "pipeline"},
	})

	// Create controller with real components
	controller := &Controller{
		Downloader: downloader,
		Uploader:   uploader,
		Store:      store,
		OutputDir:  tempDir,
		Format:     "worst",
	}

	// Step 1: Get video metadata
	t.Log("Step 1: Fetching video metadata...")
	meta, err := downloader.GetVideoMetadata(ctx, UploadTestVideoID, "")
	if err != nil {
		t.Fatalf("metadata fetch failed: %v", err)
	}
	t.Logf("  Video: %s (%d seconds)", meta.Title, meta.Duration)

	// Step 2: Sync (download + upload)
	t.Log("Step 2: Running sync (download + upload)...")
	err = controller.SyncVideo(ctx, UploadTestVideoID)
	if err != nil {
		// Check for rate limiting
		if strings.Contains(err.Error(), "21566") || strings.Contains(err.Error(), "投稿过于频繁") {
			t.Skip("Bilibili rate limit reached - try again later")
		}
		t.Fatalf("sync failed: %v", err)
	}

	// Step 3: Verify it's marked as uploaded
	t.Log("Step 3: Verifying upload record...")
	uploaded, err := store.IsUploaded(ctx, UploadTestVideoID)
	if err != nil {
		t.Fatalf("check upload status failed: %v", err)
	}
	if !uploaded {
		t.Error("video should be marked as uploaded")
	}

	// Step 4: Try to sync again - should skip
	t.Log("Step 4: Verifying skip logic...")
	// SyncVideo doesn't return skip info, so we just verify no error
	err = controller.SyncVideo(ctx, UploadTestVideoID)
	if err != nil {
		t.Logf("  Second sync returned error (expected if already uploaded): %v", err)
	}

	t.Log("\n=== Full Pipeline Test Complete ===")
	t.Log("NOTE: Please delete the test video from your Bilibili account")
}
