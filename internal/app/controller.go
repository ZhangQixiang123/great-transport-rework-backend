package app

import (
	"context"
	"fmt"
	"log/slog"
	"strings"
)

type Controller struct {
	Downloader Downloader
	Uploader   Uploader
	Store      *SQLiteStore
	OutputDir  string
	JSRuntime  string
	Format     string
}

type Uploader interface {
	Upload(path string) error
}

func (c *Controller) SyncVideo(ctx context.Context, videoID string) error {
	if c.Downloader == nil || c.Uploader == nil || c.Store == nil {
		return fmt.Errorf("controller is not fully configured")
	}

	videoURL := videoURL(videoID)
	files, err := c.Downloader.DownloadVideo(ctx, videoURL, c.OutputDir, c.JSRuntime, c.Format)
	if err != nil {
		return err
	}
	if len(files) == 0 {
		return fmt.Errorf("no files downloaded for %s", videoID)
	}
	slog.Info("video downloaded", "video_id", videoID)

	for _, path := range files {
		if err := c.Uploader.Upload(path); err != nil {
			return err
		}
		slog.Info("uploaded file", "path", path)
	}

	if err := c.Store.MarkUploaded(ctx, videoID, ""); err != nil {
		slog.Error("failed to mark uploaded", "video_id", videoID, "error", err)
		return err
	}
	return nil
}

// UploadVideo handles a full upload job: download, upload with metadata, track status.
func (c *Controller) UploadVideo(ctx context.Context, job UploadJob) (UploadJob, error) {
	if c.Downloader == nil || c.Uploader == nil || c.Store == nil {
		return job, fmt.Errorf("controller is not fully configured")
	}

	// Set per-video metadata override if uploader is BiliupUploader
	if bu, ok := c.Uploader.(*BiliupUploader); ok {
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

	// Update status to downloading
	if err := c.Store.UpdateUploadJobStatus(ctx, job.ID, "downloading", "", ""); err != nil {
		slog.Error("failed to update job status", "job_id", job.ID, "error", err)
	}

	// Download
	videoURL := videoURL(job.VideoID)
	files, err := c.Downloader.DownloadVideo(ctx, videoURL, c.OutputDir, c.JSRuntime, c.Format)
	if err != nil {
		job.Status = "failed"
		job.ErrorMessage = fmt.Sprintf("download failed: %v", err)
		_ = c.Store.UpdateUploadJobStatus(ctx, job.ID, job.Status, "", job.ErrorMessage)
		return job, err
	}
	if len(files) == 0 {
		job.Status = "failed"
		job.ErrorMessage = fmt.Sprintf("no files downloaded for %s", job.VideoID)
		_ = c.Store.UpdateUploadJobStatus(ctx, job.ID, job.Status, "", job.ErrorMessage)
		return job, fmt.Errorf("%s", job.ErrorMessage)
	}

	// Update status to uploading
	if err := c.Store.UpdateUploadJobStatus(ctx, job.ID, "uploading", "", ""); err != nil {
		slog.Error("failed to update job status", "job_id", job.ID, "error", err)
	}

	// Upload — try UploadWithResult for bvid extraction
	for _, path := range files {
		if bu, ok := c.Uploader.(*BiliupUploader); ok {
			result, err := bu.UploadWithResult(path)
			if err != nil {
				job.Status = "failed"
				job.ErrorMessage = fmt.Sprintf("upload failed: %v", err)
				_ = c.Store.UpdateUploadJobStatus(ctx, job.ID, job.Status, "", job.ErrorMessage)
				return job, err
			}
			if result != nil && result.BilibiliBvid != "" {
				job.BilibiliBvid = result.BilibiliBvid
			}
		} else {
			if err := c.Uploader.Upload(path); err != nil {
				job.Status = "failed"
				job.ErrorMessage = fmt.Sprintf("upload failed: %v", err)
				_ = c.Store.UpdateUploadJobStatus(ctx, job.ID, job.Status, "", job.ErrorMessage)
				return job, err
			}
		}
	}

	// Mark completed
	job.Status = "completed"
	_ = c.Store.UpdateUploadJobStatus(ctx, job.ID, job.Status, job.BilibiliBvid, "")
	_ = c.Store.MarkUploadedWithBvid(ctx, job.VideoID, "", job.BilibiliBvid)

	return job, nil
}
