package app

import (
	"context"
	"fmt"
	"log"
	"strings"
	"sync"
	"time"
)

// JobQueue processes upload jobs through a 3-stage pipeline:
// feedJobs → stageDownload → stageSubtitle → stageUpload.
// Each stage runs in its own goroutine with exactly one worker,
// so stages overlap across different jobs while each stage is serial.
type JobQueue struct {
	controller *Controller
	store      *SQLiteStore
	notify     chan struct{}
}

// pipelineJob carries an UploadJob and its downloaded files between stages.
type pipelineJob struct {
	job   UploadJob
	files []string
}

// NewJobQueue creates a new job queue.
func NewJobQueue(controller *Controller, store *SQLiteStore) *JobQueue {
	return &JobQueue{
		controller: controller,
		store:      store,
		notify:     make(chan struct{}, 1),
	}
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
	log.Println("Job queue pipeline started")

	jobCh := make(chan UploadJob)
	downloadedCh := make(chan pipelineJob)
	subtitledCh := make(chan pipelineJob)

	var wg sync.WaitGroup
	wg.Add(3)

	go func() {
		defer wg.Done()
		q.stageDownload(ctx, jobCh, downloadedCh)
	}()
	go func() {
		defer wg.Done()
		q.stageSubtitle(ctx, downloadedCh, subtitledCh)
	}()
	go func() {
		defer wg.Done()
		q.stageUpload(ctx, subtitledCh)
	}()

	// Feed jobs into the pipeline, then close to cascade shutdown.
	q.feedJobs(ctx, jobCh)
	close(jobCh)

	wg.Wait()
	log.Println("Job queue pipeline shut down")
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
			log.Printf("queue: failed to get next pending job: %v", err)
			return
		}
		if job == nil {
			return // no more pending jobs
		}

		// Mark as downloading immediately to prevent re-fetch.
		if err := q.store.UpdateUploadJobStatus(ctx, job.ID, "downloading", "", ""); err != nil {
			log.Printf("queue: failed to mark job %d as downloading: %v", job.ID, err)
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

	log.Printf("queue: downloading job %d (video=%s)", job.ID, job.VideoID)

	defer func() {
		if r := recover(); r != nil {
			errMsg := fmt.Sprintf("download panic: %v", r)
			log.Printf("queue: job %d panicked in download: %s", job.ID, errMsg)
			_ = q.store.UpdateUploadJobStatus(parentCtx, job.ID, "failed", "", errMsg)
		}
	}()

	videoURL := videoURL(job.VideoID)
	files, err := q.controller.Downloader.DownloadVideo(ctx, videoURL, q.controller.OutputDir, q.controller.JSRuntime, q.controller.Format)
	if err != nil {
		errMsg := fmt.Sprintf("download failed: %v", err)
		log.Printf("queue: job %d download failed: %v", job.ID, err)
		_ = q.store.UpdateUploadJobStatus(parentCtx, job.ID, "failed", "", errMsg)
		return nil, false
	}
	if len(files) == 0 {
		errMsg := fmt.Sprintf("no files downloaded for %s", job.VideoID)
		log.Printf("queue: job %d: %s", job.ID, errMsg)
		_ = q.store.UpdateUploadJobStatus(parentCtx, job.ID, "failed", "", errMsg)
		return nil, false
	}

	log.Printf("queue: job %d downloaded %d file(s)", job.ID, len(files))
	return files, true
}

// ---------------------------------------------------------------------------
// Stage 2: Subtitle
// ---------------------------------------------------------------------------

func (q *JobQueue) stageSubtitle(ctx context.Context, in <-chan pipelineJob, out chan<- pipelineJob) {
	defer close(out)
	for pj := range in {
		if ctx.Err() != nil {
			return
		}
		q.doSubtitle(ctx, &pj)
		select {
		case out <- pj:
		case <-ctx.Done():
			return
		}
	}
}

func (q *JobQueue) doSubtitle(parentCtx context.Context, pj *pipelineJob) {
	if q.controller.SubtitleGenerator == nil {
		return // pass-through
	}

	ctx, cancel := context.WithTimeout(parentCtx, 15*time.Minute)
	defer cancel()

	log.Printf("queue: subtitling job %d", pj.job.ID)

	defer func() {
		if r := recover(); r != nil {
			log.Printf("queue: job %d panicked in subtitle: %v (continuing)", pj.job.ID, r)
		}
	}()

	if err := q.store.UpdateUploadJobStatus(parentCtx, pj.job.ID, "subtitling", "", ""); err != nil {
		log.Printf("queue: failed to update job %d status to subtitling: %v", pj.job.ID, err)
	}

	for _, path := range pj.files {
		if err := q.controller.SubtitleGenerator.Generate(ctx, path); err != nil {
			log.Printf("WARNING: subtitle generation failed for job %d file %s: %v (continuing without subtitles)", pj.job.ID, path, err)
		}
	}
}

// ---------------------------------------------------------------------------
// Stage 3: Upload
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
	log.Printf("queue: uploading job %d (video=%s)", job.ID, job.VideoID)

	defer func() {
		if r := recover(); r != nil {
			errMsg := fmt.Sprintf("upload panic: %v", r)
			log.Printf("queue: job %d panicked in upload: %s", job.ID, errMsg)
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
		log.Printf("queue: failed to update job %d status to uploading: %v", job.ID, err)
	}

	var bvid string
	for _, path := range pj.files {
		if bu, ok := q.controller.Uploader.(*BiliupUploader); ok {
			result, err := bu.UploadWithResult(path)
			if err != nil {
				errMsg := fmt.Sprintf("upload failed: %v", err)
				log.Printf("queue: job %d upload failed: %v", job.ID, err)
				_ = q.store.UpdateUploadJobStatus(parentCtx, job.ID, "failed", "", errMsg)
				return
			}
			if result != nil && result.BilibiliBvid != "" {
				bvid = result.BilibiliBvid
			}
		} else {
			if err := q.controller.Uploader.Upload(path); err != nil {
				errMsg := fmt.Sprintf("upload failed: %v", err)
				log.Printf("queue: job %d upload failed: %v", job.ID, err)
				_ = q.store.UpdateUploadJobStatus(parentCtx, job.ID, "failed", "", errMsg)
				return
			}
		}
	}

	// Mark completed.
	_ = q.store.UpdateUploadJobStatus(ctx, job.ID, "completed", bvid, "")
	_ = q.store.MarkUploadedWithBvid(ctx, job.VideoID, "", bvid)
	log.Printf("queue: job %d completed (bvid=%s)", job.ID, bvid)
}
