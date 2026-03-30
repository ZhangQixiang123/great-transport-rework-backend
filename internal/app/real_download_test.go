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
)

func skipIfNoYtDlp(t *testing.T) {
	if !HasExecutable("yt-dlp") {
		t.Skip("yt-dlp not found in PATH - skipping real download test")
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

// TestReal_FullPipeline_SyncVideo tests the SyncVideo controller method end-to-end.
// Run with: ENABLE_UPLOAD_TEST=1 go test -v ./internal/app -tags=integration -run "TestReal_FullPipeline_SyncVideo"
func TestReal_FullPipeline_SyncVideo(t *testing.T) {
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

	// Step 1: Sync (download + upload)
	t.Log("Step 1: Running SyncVideo (download + upload)...")
	err = controller.SyncVideo(ctx, UploadTestVideoID)
	if err != nil {
		// Check for rate limiting
		if strings.Contains(err.Error(), "21566") || strings.Contains(err.Error(), "投稿过于频繁") {
			t.Skip("Bilibili rate limit reached - try again later")
		}
		t.Fatalf("sync failed: %v", err)
	}

	// Step 2: Verify it's marked as uploaded
	t.Log("Step 2: Verifying upload record...")
	uploaded, err := store.IsUploaded(ctx, UploadTestVideoID)
	if err != nil {
		t.Fatalf("check upload status failed: %v", err)
	}
	if !uploaded {
		t.Error("video should be marked as uploaded")
	}

	t.Log("\n=== Full Pipeline Test Complete ===")
	t.Log("NOTE: Please delete the test video from your Bilibili account")
}
