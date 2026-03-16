package app

import (
	"context"
	"os"
	"sync"
	"testing"
	"time"
)

type fakeDownloader struct{}

func (d fakeDownloader) ListChannelVideoIDs(ctx context.Context, channelURL string, limit int, jsRuntime string) ([]string, error) {
	return nil, nil
}
func (d fakeDownloader) DownloadVideo(ctx context.Context, videoURL, outputDir string, jsRuntime, format string) ([]string, error) {
	// Simulate downloading by creating a temp file.
	f, err := os.CreateTemp(outputDir, "video-*.mp4")
	if err != nil {
		return nil, err
	}
	f.Close()
	return []string{f.Name()}, nil
}
func (d fakeDownloader) GetVideoMetadata(ctx context.Context, videoID string, jsRuntime string) (*VideoMetadata, error) {
	return nil, nil
}
func (d fakeDownloader) GetChannelVideosMetadata(ctx context.Context, channelURL string, limit int, jsRuntime string) ([]VideoMetadata, error) {
	return nil, nil
}

type fakeUploader struct{}

func (u fakeUploader) Upload(path string) error {
	return nil
}

func TestJobQueueProcessesJobs(t *testing.T) {
	f, err := os.CreateTemp("", "test-queue-*.db")
	if err != nil {
		t.Fatal(err)
	}
	dbPath := f.Name()
	f.Close()
	defer os.Remove(dbPath)

	store, err := NewSQLiteStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}

	ctx := context.Background()
	if err := store.EnsureSchema(ctx); err != nil {
		t.Fatal(err)
	}

	outDir, err := os.MkdirTemp("", "test-queue-out-*")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(outDir)

	controller := &Controller{
		Downloader: fakeDownloader{},
		Uploader:   fakeUploader{},
		Store:      store,
		OutputDir:  outDir,
	}

	// Create 2 pending jobs.
	id1, _ := store.CreateUploadJob(ctx, "vid1", "Title 1", "Desc", "")
	id2, _ := store.CreateUploadJob(ctx, "vid2", "Title 2", "Desc", "")

	// Create queue and start with cancellable context.
	qCtx, cancel := context.WithCancel(ctx)
	defer cancel()

	queue := NewJobQueue(controller, store)
	queue.Start(qCtx)

	// Notify the queue.
	queue.Enqueue()

	// Wait for jobs to be processed (with timeout).
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		job1, _ := store.GetUploadJob(ctx, id1)
		job2, _ := store.GetUploadJob(ctx, id2)
		if job1 != nil && job1.Status == "completed" &&
			job2 != nil && job2.Status == "completed" {
			return // success
		}
		time.Sleep(100 * time.Millisecond)
	}

	// Check final state.
	job1, _ := store.GetUploadJob(ctx, id1)
	job2, _ := store.GetUploadJob(ctx, id2)
	t.Fatalf("jobs not completed in time: job1=%s, job2=%s",
		job1.Status, job2.Status)
}

func TestJobQueueDrainsOnStartup(t *testing.T) {
	f, err := os.CreateTemp("", "test-queue-drain-*.db")
	if err != nil {
		t.Fatal(err)
	}
	dbPath := f.Name()
	f.Close()
	defer os.Remove(dbPath)

	store, err := NewSQLiteStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}

	ctx := context.Background()
	if err := store.EnsureSchema(ctx); err != nil {
		t.Fatal(err)
	}

	outDir, err := os.MkdirTemp("", "test-queue-drain-out-*")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(outDir)

	controller := &Controller{
		Downloader: fakeDownloader{},
		Uploader:   fakeUploader{},
		Store:      store,
		OutputDir:  outDir,
	}

	// Create a pending job BEFORE starting the queue.
	id1, _ := store.CreateUploadJob(ctx, "vid-startup", "Startup", "Desc", "")

	qCtx, cancel := context.WithCancel(ctx)
	defer cancel()

	queue := NewJobQueue(controller, store)
	queue.Start(qCtx) // should drain on startup without Enqueue()

	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		job, _ := store.GetUploadJob(ctx, id1)
		if job != nil && job.Status == "completed" {
			return // success
		}
		time.Sleep(100 * time.Millisecond)
	}

	job, _ := store.GetUploadJob(ctx, id1)
	t.Fatalf("startup drain job not completed: status=%s", job.Status)
}

func TestJobQueueEnqueueNonBlocking(t *testing.T) {
	f, err := os.CreateTemp("", "test-queue-enqueue-*.db")
	if err != nil {
		t.Fatal(err)
	}
	dbPath := f.Name()
	f.Close()
	defer os.Remove(dbPath)

	store, err := NewSQLiteStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}

	controller := &Controller{Store: store}
	queue := NewJobQueue(controller, store)

	// Multiple rapid enqueues should not block.
	for i := 0; i < 100; i++ {
		queue.Enqueue()
	}
}

// slowDownloader records when each download starts and sleeps for a duration.
type slowDownloader struct {
	mu     sync.Mutex
	starts []time.Time
	delay  time.Duration
}

func (d *slowDownloader) ListChannelVideoIDs(ctx context.Context, channelURL string, limit int, jsRuntime string) ([]string, error) {
	return nil, nil
}
func (d *slowDownloader) DownloadVideo(ctx context.Context, videoURL, outputDir string, jsRuntime, format string) ([]string, error) {
	d.mu.Lock()
	d.starts = append(d.starts, time.Now())
	d.mu.Unlock()

	select {
	case <-time.After(d.delay):
	case <-ctx.Done():
		return nil, ctx.Err()
	}

	f, err := os.CreateTemp(outputDir, "video-*.mp4")
	if err != nil {
		return nil, err
	}
	f.Close()
	return []string{f.Name()}, nil
}
func (d *slowDownloader) GetVideoMetadata(ctx context.Context, videoID string, jsRuntime string) (*VideoMetadata, error) {
	return nil, nil
}
func (d *slowDownloader) GetChannelVideosMetadata(ctx context.Context, channelURL string, limit int, jsRuntime string) ([]VideoMetadata, error) {
	return nil, nil
}

// slowUploader records when each upload starts and sleeps for a duration.
type slowUploader struct {
	mu     sync.Mutex
	starts []time.Time
	delay  time.Duration
}

func (u *slowUploader) Upload(path string) error {
	u.mu.Lock()
	u.starts = append(u.starts, time.Now())
	u.mu.Unlock()

	time.Sleep(u.delay)
	return nil
}

func TestJobQueuePipelineOverlap(t *testing.T) {
	f, err := os.CreateTemp("", "test-queue-pipeline-*.db")
	if err != nil {
		t.Fatal(err)
	}
	dbPath := f.Name()
	f.Close()
	defer os.Remove(dbPath)

	store, err := NewSQLiteStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}

	ctx := context.Background()
	if err := store.EnsureSchema(ctx); err != nil {
		t.Fatal(err)
	}

	outDir, err := os.MkdirTemp("", "test-queue-pipeline-out-*")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(outDir)

	dl := &slowDownloader{delay: 200 * time.Millisecond}
	ul := &slowUploader{delay: 200 * time.Millisecond}

	controller := &Controller{
		Downloader: dl,
		Uploader:   ul,
		Store:      store,
		OutputDir:  outDir,
	}

	// Create 3 pending jobs.
	id1, _ := store.CreateUploadJob(ctx, "pipe1", "Pipe 1", "Desc", "")
	id2, _ := store.CreateUploadJob(ctx, "pipe2", "Pipe 2", "Desc", "")
	id3, _ := store.CreateUploadJob(ctx, "pipe3", "Pipe 3", "Desc", "")

	qCtx, cancel := context.WithCancel(ctx)
	defer cancel()

	queue := NewJobQueue(controller, store)
	queue.Start(qCtx)
	queue.Enqueue()

	// Wait for all 3 jobs to complete.
	deadline := time.Now().Add(15 * time.Second)
	for time.Now().Before(deadline) {
		j1, _ := store.GetUploadJob(ctx, id1)
		j2, _ := store.GetUploadJob(ctx, id2)
		j3, _ := store.GetUploadJob(ctx, id3)
		if j1 != nil && j1.Status == "completed" &&
			j2 != nil && j2.Status == "completed" &&
			j3 != nil && j3.Status == "completed" {
			break
		}
		time.Sleep(50 * time.Millisecond)
	}

	// Verify all completed.
	j1, _ := store.GetUploadJob(ctx, id1)
	j2, _ := store.GetUploadJob(ctx, id2)
	j3, _ := store.GetUploadJob(ctx, id3)
	if j1.Status != "completed" || j2.Status != "completed" || j3.Status != "completed" {
		t.Fatalf("not all jobs completed: j1=%s, j2=%s, j3=%s", j1.Status, j2.Status, j3.Status)
	}

	// Verify pipeline overlap: job 2 should start downloading before job 1
	// finishes uploading. With a serial queue, download 2 would start after
	// upload 1 finishes. With a pipeline, download 2 starts as soon as
	// download 1 finishes (while upload 1 hasn't started yet or is in progress).
	dl.mu.Lock()
	dlStarts := dl.starts
	dl.mu.Unlock()
	ul.mu.Lock()
	ulStarts := ul.starts
	ul.mu.Unlock()

	if len(dlStarts) < 2 || len(ulStarts) < 1 {
		t.Fatalf("expected at least 2 download starts and 1 upload start, got dl=%d ul=%d",
			len(dlStarts), len(ulStarts))
	}

	// Download of job 2 should start before upload of job 1 finishes.
	// Upload 1 finishes at ulStarts[0] + delay. Download 2 starts at dlStarts[1].
	upload1End := ulStarts[0].Add(ul.delay)
	download2Start := dlStarts[1]
	if !download2Start.Before(upload1End) {
		t.Errorf("pipeline overlap not detected: download2 started at %v, upload1 ended at %v",
			download2Start, upload1End)
	}
}
