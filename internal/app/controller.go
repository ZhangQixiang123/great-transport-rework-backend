package app

import (
	"context"
	"fmt"
	"log"
)

type SyncResult struct {
	Considered int
	Skipped    int
	Downloaded int
	Uploaded   int
}

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
			log.Printf("Uploaded the file: &s", path)
		}
	}

	if err := c.Store.MarkUploaded(ctx, videoID, channelID); err != nil {
		log.Printf("failed to mark uploaded for %s: %v", videoID, err)
		return err
	}
	return nil
}
