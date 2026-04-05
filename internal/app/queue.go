package app

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"strings"
	"sync"
	"time"
)

// JobQueue processes upload jobs through a pipeline:
// feedJobs → stageDownload → stageUpload (+ subtitle generation after upload).
// Each stage runs in its own goroutine with exactly one worker,
// so stages overlap across different jobs while each stage is serial.
type JobQueue struct {
	controller *Controller
	store      *SQLiteStore
	notify     chan struct{}
	subtitleCfg *SubtitlePipelineConfig // nil = skip subtitle generation
}

// pipelineJob carries an UploadJob and its downloaded files between stages.
type pipelineJob struct {
	job   UploadJob
	files []string
}

// SubtitleOptions configures optional subtitle pipeline settings.
type SubtitleOptions struct {
	MLServiceDir string // path to ml-service directory
	LLMBackend   string // LLM backend for annotations (ollama, openai, anthropic)
}

// NewJobQueue creates a new job queue.
// Subtitle generation is automatically enabled if a BiliupUploader with a
// cookie path is configured on the controller.
func NewJobQueue(controller *Controller, store *SQLiteStore, opts ...SubtitleOptions) *JobQueue {
	q := &JobQueue{
		controller: controller,
		store:      store,
		notify:     make(chan struct{}, 1),
	}

	// Auto-configure subtitle pipeline from existing uploader settings.
	if bu, ok := controller.Uploader.(*BiliupUploader); ok && bu.opts.CookiePath != "" {
		q.subtitleCfg = &SubtitlePipelineConfig{
			PythonBinary:  "ml-service/.venv/Scripts/python",
			WhisperScript: "scripts/whisper_transcribe.py",
			WhisperModel:  "base",
			CookiePath:    bu.opts.CookiePath,
		}
		if len(opts) > 0 {
			if opts[0].MLServiceDir != "" {
				q.subtitleCfg.MLServiceDir = opts[0].MLServiceDir
				q.subtitleCfg.LLMBackend = opts[0].LLMBackend
				slog.Info("queue: annotation enabled (CLI mode)", "ml_service_dir", opts[0].MLServiceDir, "backend", opts[0].LLMBackend)
			}
		}
		slog.Info("queue: subtitle pipeline enabled", "model", q.subtitleCfg.WhisperModel, "cookie", q.subtitleCfg.CookiePath)
	}

	return q
}

// GetSubtitleConfig returns the subtitle pipeline config (nil if not configured).
func (q *JobQueue) GetSubtitleConfig() *SubtitlePipelineConfig {
	return q.subtitleCfg
}

// Enqueue sends a non-blocking notification that a new job is available.
func (q *JobQueue) Enqueue() {
	select {
	case q.notify <- struct{}{}:
	default:
	}
}

// Start runs the pipeline workers in background goroutines.
func (q *JobQueue) Start(ctx context.Context) {
	go q.run(ctx)
}

func (q *JobQueue) run(ctx context.Context) {
	// Recover jobs orphaned by a previous crash (stuck in downloading/uploading).
	if recovered, err := q.store.RecoverOrphanedJobs(ctx); err != nil {
		slog.Error("queue: failed to recover orphaned jobs", "error", err)
	} else if recovered > 0 {
		slog.Warn("queue: recovered orphaned jobs", "count", recovered)
	}

	slog.Info("job queue pipeline started")

	jobCh := make(chan UploadJob)
	downloadedCh := make(chan pipelineJob)

	var wg sync.WaitGroup
	wg.Add(2)

	go func() {
		defer wg.Done()
		q.stageDownload(ctx, jobCh, downloadedCh)
	}()
	go func() {
		defer wg.Done()
		q.stageUpload(ctx, downloadedCh)
	}()

	// Feed jobs into the pipeline, then close to cascade shutdown.
	q.feedJobs(ctx, jobCh)
	close(jobCh)

	wg.Wait()
	slog.Info("job queue pipeline shut down")
}

// feedJobs drains pending jobs from DB on startup and on each notify signal.
func (q *JobQueue) feedJobs(ctx context.Context, jobCh chan<- UploadJob) {
	// Drain any jobs left pending from a previous run.
	q.drainPendingInto(ctx, jobCh)

	for {
		select {
		case <-ctx.Done():
			return
		case <-q.notify:
			q.drainPendingInto(ctx, jobCh)
		}
	}
}

// drainPendingInto fetches all pending jobs from DB and sends them into the
// pipeline. Each job is immediately marked as "downloading" to prevent
// duplicate fetches if another Enqueue fires while the job sits in a channel.
func (q *JobQueue) drainPendingInto(ctx context.Context, jobCh chan<- UploadJob) {
	for {
		if ctx.Err() != nil {
			return
		}

		job, err := q.store.GetNextPendingJob(ctx)
		if err != nil {
			slog.Error("queue: failed to get next pending job", "error", err)
			return
		}
		if job == nil {
			return // no more pending jobs
		}

		// Mark as downloading immediately to prevent re-fetch.
		if err := q.store.UpdateUploadJobStatus(ctx, job.ID, "downloading", "", ""); err != nil {
			slog.Error("queue: failed to mark job as downloading", "job_id", job.ID, "error", err)
			continue
		}
		job.Status = "downloading"

		select {
		case jobCh <- *job:
		case <-ctx.Done():
			return
		}
	}
}

// ---------------------------------------------------------------------------
// Stage 1: Download
// ---------------------------------------------------------------------------

func (q *JobQueue) stageDownload(ctx context.Context, in <-chan UploadJob, out chan<- pipelineJob) {
	defer close(out)
	for job := range in {
		if ctx.Err() != nil {
			return
		}
		files, ok := q.doDownload(ctx, job)
		if !ok {
			continue // job already marked failed
		}
		select {
		case out <- pipelineJob{job: job, files: files}:
		case <-ctx.Done():
			return
		}
	}
}

func (q *JobQueue) doDownload(parentCtx context.Context, job UploadJob) ([]string, bool) {
	ctx, cancel := context.WithTimeout(parentCtx, 10*time.Minute)
	defer cancel()

	slog.Info("queue: downloading", "job_id", job.ID, "video_id", job.VideoID)

	defer func() {
		if r := recover(); r != nil {
			errMsg := fmt.Sprintf("download panic: %v", r)
			slog.Error("queue: job panicked in download", "job_id", job.ID, "error", errMsg)
			_ = q.store.UpdateUploadJobStatus(parentCtx, job.ID, "failed", "", errMsg)
		}
	}()

	videoURL := videoURL(job.VideoID)
	files, err := q.controller.Downloader.DownloadVideo(ctx, videoURL, q.controller.OutputDir, q.controller.JSRuntime, q.controller.Format)
	if err != nil {
		errMsg := fmt.Sprintf("download failed: %v", err)
		slog.Error("queue: download failed", "job_id", job.ID, "error", err)
		_ = q.store.UpdateUploadJobStatus(parentCtx, job.ID, "failed", "", errMsg)
		return nil, false
	}
	if len(files) == 0 {
		errMsg := fmt.Sprintf("no files downloaded for %s", job.VideoID)
		slog.Error("queue: no files downloaded", "job_id", job.ID, "video_id", job.VideoID)
		_ = q.store.UpdateUploadJobStatus(parentCtx, job.ID, "failed", "", errMsg)
		return nil, false
	}

	slog.Info("queue: download complete", "job_id", job.ID, "file_count", len(files))

	// Store downloaded file paths in the job record.
	if filesJSON, err := json.Marshal(files); err == nil {
		if err := q.store.UpdateUploadJobFiles(parentCtx, job.ID, string(filesJSON)); err != nil {
			slog.Error("queue: failed to store download files", "job_id", job.ID, "error", err)
		}
	}

	return files, true
}

// ---------------------------------------------------------------------------
// Stage 2: Upload
// ---------------------------------------------------------------------------

func (q *JobQueue) stageUpload(ctx context.Context, in <-chan pipelineJob) {
	for pj := range in {
		if ctx.Err() != nil {
			return
		}
		q.doUpload(ctx, pj)
	}
}

func (q *JobQueue) doUpload(parentCtx context.Context, pj pipelineJob) {
	ctx, cancel := context.WithTimeout(parentCtx, 10*time.Minute)
	defer cancel()

	job := pj.job
	slog.Info("queue: uploading", "job_id", job.ID, "video_id", job.VideoID)

	defer func() {
		if r := recover(); r != nil {
			errMsg := fmt.Sprintf("upload panic: %v", r)
			slog.Error("queue: job panicked in upload", "job_id", job.ID, "error", errMsg)
			_ = q.store.UpdateUploadJobStatus(parentCtx, job.ID, "failed", "", errMsg)
		}
	}()

	// Set per-video metadata override if uploader is BiliupUploader.
	if bu, ok := q.controller.Uploader.(*BiliupUploader); ok {
		var tags []string
		if job.Tags != "" {
			for _, t := range strings.Split(job.Tags, ",") {
				t = strings.TrimSpace(t)
				if t != "" {
					tags = append(tags, t)
				}
			}
		}
		bu.SetVideoMeta(job.Title, job.Description, tags)
	}

	if err := q.store.UpdateUploadJobStatus(parentCtx, job.ID, "uploading", "", ""); err != nil {
		slog.Error("queue: failed to update status", "job_id", job.ID, "status", "uploading", "error", err)
	}

	var bvid string
	for _, path := range pj.files {
		if bu, ok := q.controller.Uploader.(*BiliupUploader); ok {
			result, err := bu.UploadWithResult(path)
			if err != nil {
				errMsg := fmt.Sprintf("upload failed: %v", err)
				slog.Error("queue: upload failed", "job_id", job.ID, "error", err)
				_ = q.store.UpdateUploadJobStatus(parentCtx, job.ID, "failed", "", errMsg)
				return
			}
			if result != nil && result.BilibiliBvid != "" {
				bvid = result.BilibiliBvid
			}
		} else {
			if err := q.controller.Uploader.Upload(path); err != nil {
				errMsg := fmt.Sprintf("upload failed: %v", err)
				slog.Error("queue: upload failed", "job_id", job.ID, "error", err)
				_ = q.store.UpdateUploadJobStatus(parentCtx, job.ID, "failed", "", errMsg)
				return
			}
		}
	}

	// Mark completed.
	_ = q.store.UpdateUploadJobStatus(ctx, job.ID, "completed", bvid, "")
	_ = q.store.MarkUploadedWithBvid(ctx, job.VideoID, "", bvid)
	slog.Info("queue: job completed", "job_id", job.ID, "bvid", bvid)

	// Subtitle generation (non-blocking, runs after upload).
	if q.subtitleCfg != nil && bvid != "" && len(pj.files) > 0 {
		go q.doSubtitle(parentCtx, job.ID, bvid, pj.files[0])
	}
}

func (q *JobQueue) doSubtitle(ctx context.Context, jobID int64, bvid, videoPath string) {
	defer func() {
		if r := recover(); r != nil {
			slog.Error("queue: subtitle panicked", "job_id", jobID, "error", r)
			_ = q.store.UpdateSubtitleStatus(ctx, jobID, "failed")
		}
	}()

	_ = q.store.UpdateSubtitleStatus(ctx, jobID, "generating")

	subtitleCtx, cancel := context.WithTimeout(ctx, 30*time.Minute)
	defer cancel()

	if err := RunSubtitlePipeline(subtitleCtx, *q.subtitleCfg, q.store, jobID, videoPath, bvid); err != nil {
		slog.Error("queue: subtitle failed", "job_id", jobID, "error", err)
		_ = q.store.UpdateSubtitleStatus(ctx, jobID, "failed")
		_ = q.store.UpdateUploadJobStatus(ctx, jobID, "completed", bvid, err.Error())
		return
	}
	// Pipeline sets status to "review" — awaiting human approval via /upload/subtitle-approve
	slog.Info("queue: subtitle draft ready for review", "job_id", jobID)
}
