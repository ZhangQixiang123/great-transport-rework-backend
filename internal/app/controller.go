package app

import (
	"context"
	"fmt"
	"log"
	"strings"
)

type SyncResult struct {
	Considered int
	Skipped    int
	Downloaded int
	Uploaded   int
}

type Controller struct {
	Downloader         Downloader
	Uploader           Uploader
	Store              *SQLiteStore
	OutputDir          string
	JSRuntime          string
	Format             string
	SubtitleGenerator  *SubtitleGenerator
}

type Uploader interface {
	Upload(path string) error
}

func (c *Controller) SyncChannel(ctx context.Context, channelID string, limit int) (SyncResult, error) {
	if c.Downloader == nil || c.Uploader == nil || c.Store == nil {
		return SyncResult{}, fmt.Errorf("controller is not fully configured")
	}
	channelURL := channelURL(channelID)
	ids, err := c.Downloader.ListChannelVideoIDs(ctx, channelURL, limit, c.JSRuntime)
	if err != nil {
		return SyncResult{}, err
	}

	result := SyncResult{Considered: len(ids)}
	for _, id := range ids {
		uploaded, err := c.Store.IsUploaded(ctx, id)
		if err != nil {
			return result, err
		}
		if uploaded {
			result.Skipped++
			continue
		}

		if err := c.syncVideoByID(ctx, id, channelID, &result); err != nil {
			return result, err
		}
	}
	return result, nil
}

func (c *Controller) SyncVideo(ctx context.Context, videoID string) error {
	if c.Downloader == nil || c.Uploader == nil || c.Store == nil {
		return fmt.Errorf("controller is not fully configured")
	}
	return c.syncVideoByID(ctx, videoID, "", nil)
}

func (c *Controller) syncVideoByID(ctx context.Context, videoID, channelID string, result *SyncResult) error {
	videoURL := videoURL(videoID)
	files, err := c.Downloader.DownloadVideo(ctx, videoURL, c.OutputDir, c.JSRuntime, c.Format)
	if err != nil {
		return err
	}
	if len(files) == 0 {
		return fmt.Errorf("no files downloaded for %s", videoID)
	}
	if result != nil {
		result.Downloaded += len(files)
		log.Printf("Video of id %s is downloaded", videoID)
	}

	for _, path := range files {
		if err := c.Uploader.Upload(path); err != nil {
			return err
		}
		if result != nil {
			result.Uploaded++
			log.Printf("Uploaded the file: %s", path)
		}
	}

	if err := c.Store.MarkUploaded(ctx, videoID, channelID); err != nil {
		log.Printf("failed to mark uploaded for %s: %v", videoID, err)
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
		log.Printf("failed to update job %d status: %v", job.ID, err)
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

	// Subtitle generation (non-fatal)
	if c.SubtitleGenerator != nil {
		if err := c.Store.UpdateUploadJobStatus(ctx, job.ID, "subtitling", "", ""); err != nil {
			log.Printf("failed to update job %d status: %v", job.ID, err)
		}
		for _, path := range files {
			if err := c.SubtitleGenerator.Generate(ctx, path); err != nil {
				log.Printf("WARNING: subtitle generation failed for %s: %v (continuing without subtitles)", path, err)
			}
		}
	}

	// Update status to uploading
	if err := c.Store.UpdateUploadJobStatus(ctx, job.ID, "uploading", "", ""); err != nil {
		log.Printf("failed to update job %d status: %v", job.ID, err)
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
