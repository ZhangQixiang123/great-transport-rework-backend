package app

import (
	"context"
	"os"
	"testing"
)

func TestMarkUploadedAndIsUploaded(t *testing.T) {
	f, err := os.CreateTemp("", "test-mark-uploaded-*.db")
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

	// Not uploaded yet
	uploaded, err := store.IsUploaded(ctx, "vid123")
	if err != nil {
		t.Fatalf("IsUploaded: %v", err)
	}
	if uploaded {
		t.Fatal("expected not uploaded")
	}

	// Mark as uploaded
	if err := store.MarkUploaded(ctx, "vid123", "UC123"); err != nil {
		t.Fatalf("MarkUploaded: %v", err)
	}

	// Should be uploaded now
	uploaded, err = store.IsUploaded(ctx, "vid123")
	if err != nil {
		t.Fatalf("IsUploaded: %v", err)
	}
	if !uploaded {
		t.Fatal("expected uploaded")
	}
}

func TestMarkUploadedWithBvid(t *testing.T) {
	f, err := os.CreateTemp("", "test-mark-bvid-*.db")
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

	// Mark uploaded with bvid
	bvid := "BV1AB411c7XY"
	if err := store.MarkUploadedWithBvid(ctx, "vid123", "UC123", bvid); err != nil {
		t.Fatalf("MarkUploadedWithBvid: %v", err)
	}

	// Should be uploaded
	uploaded, err := store.IsUploaded(ctx, "vid123")
	if err != nil {
		t.Fatalf("IsUploaded: %v", err)
	}
	if !uploaded {
		t.Fatal("expected uploaded")
	}

	// Update with empty bvid should not overwrite existing bvid (COALESCE)
	if err := store.MarkUploadedWithBvid(ctx, "vid123", "UC123", ""); err != nil {
		t.Fatalf("MarkUploadedWithBvid (empty): %v", err)
	}
	uploaded, _ = store.IsUploaded(ctx, "vid123")
	if !uploaded {
		t.Fatal("expected still uploaded after re-mark")
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

func TestUpdateUploadJobFiles(t *testing.T) {
	f, err := os.CreateTemp("", "test-job-files-*.db")
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

	jobID, _ := store.CreateUploadJob(ctx, "vid_files", "Title", "Desc", "")

	filesJSON := `["/tmp/video.mp4"]`
	if err := store.UpdateUploadJobFiles(ctx, jobID, filesJSON); err != nil {
		t.Fatalf("UpdateUploadJobFiles: %v", err)
	}

	job, _ := store.GetUploadJob(ctx, jobID)
	if job.DownloadFiles != filesJSON {
		t.Errorf("DownloadFiles: got %q, want %q", job.DownloadFiles, filesJSON)
	}
}

func TestGetAllUploadedVideoIDs(t *testing.T) {
	f, err := os.CreateTemp("", "test-all-uploaded-*.db")
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

	// Empty initially
	ids, err := store.GetAllUploadedVideoIDs(ctx)
	if err != nil {
		t.Fatalf("GetAllUploadedVideoIDs (empty): %v", err)
	}
	if len(ids) != 0 {
		t.Fatalf("expected 0 IDs, got %d", len(ids))
	}

	// Add to uploads table
	store.MarkUploaded(ctx, "vid1", "UC1")
	store.MarkUploaded(ctx, "vid2", "UC2")

	// Add to upload_jobs table
	store.CreateUploadJob(ctx, "vid3", "T", "D", "")
	jobID, _ := store.CreateUploadJob(ctx, "vid4", "T", "D", "")
	store.UpdateUploadJobStatus(ctx, jobID, "failed", "", "error")

	// vid1, vid2 from uploads; vid3 from jobs (pending); vid4 is failed so excluded
	ids, err = store.GetAllUploadedVideoIDs(ctx)
	if err != nil {
		t.Fatalf("GetAllUploadedVideoIDs: %v", err)
	}
	idSet := make(map[string]bool)
	for _, id := range ids {
		idSet[id] = true
	}
	if !idSet["vid1"] || !idSet["vid2"] || !idSet["vid3"] {
		t.Errorf("expected vid1, vid2, vid3 in results, got %v", ids)
	}
	if idSet["vid4"] {
		t.Error("failed job vid4 should not be in results")
	}

	// Test UNION dedup: add vid1 to jobs too — should appear once
	store.CreateUploadJob(ctx, "vid1", "T", "D", "")
	ids, err = store.GetAllUploadedVideoIDs(ctx)
	if err != nil {
		t.Fatalf("GetAllUploadedVideoIDs (dedup): %v", err)
	}
	count := 0
	for _, id := range ids {
		if id == "vid1" {
			count++
		}
	}
	if count != 1 {
		t.Errorf("vid1 should appear once due to UNION, appeared %d times", count)
	}
}

func TestFindActiveUploadJob(t *testing.T) {
	f, err := os.CreateTemp("", "test-find-active-*.db")
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

	// No jobs — should return nil
	job, err := store.FindActiveUploadJob(ctx, "vid1")
	if err != nil {
		t.Fatalf("FindActiveUploadJob (empty): %v", err)
	}
	if job != nil {
		t.Fatal("expected nil for nonexistent video")
	}

	// Create a pending job
	jobID, _ := store.CreateUploadJob(ctx, "vid1", "T", "D", "")

	job, err = store.FindActiveUploadJob(ctx, "vid1")
	if err != nil {
		t.Fatalf("FindActiveUploadJob: %v", err)
	}
	if job == nil {
		t.Fatal("expected active job")
	}
	if job.ID != jobID {
		t.Errorf("expected job ID %d, got %d", jobID, job.ID)
	}

	// Mark as failed — should no longer be found
	store.UpdateUploadJobStatus(ctx, jobID, "failed", "", "error")

	job, err = store.FindActiveUploadJob(ctx, "vid1")
	if err != nil {
		t.Fatalf("FindActiveUploadJob (after fail): %v", err)
	}
	if job != nil {
		t.Error("expected nil for failed job")
	}

	// Create a new completing job — should find that one
	jobID2, _ := store.CreateUploadJob(ctx, "vid1", "T2", "D2", "")
	store.UpdateUploadJobStatus(ctx, jobID2, "downloading", "", "")

	job, err = store.FindActiveUploadJob(ctx, "vid1")
	if err != nil {
		t.Fatalf("FindActiveUploadJob (downloading): %v", err)
	}
	if job == nil {
		t.Fatal("expected active job")
	}
	if job.ID != jobID2 {
		t.Errorf("expected job ID %d, got %d", jobID2, job.ID)
	}
}

func TestUpdateSubtitleStatus(t *testing.T) {
	f, err := os.CreateTemp("", "test-subtitle-status-*.db")
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

	jobID, _ := store.CreateUploadJob(ctx, "vid_sub", "Title", "Desc", "")

	if err := store.UpdateSubtitleStatus(ctx, jobID, "generating"); err != nil {
		t.Fatalf("UpdateSubtitleStatus: %v", err)
	}

	job, _ := store.GetUploadJob(ctx, jobID)
	if job.SubtitleStatus != "generating" {
		t.Errorf("SubtitleStatus: got %q, want %q", job.SubtitleStatus, "generating")
	}
}
