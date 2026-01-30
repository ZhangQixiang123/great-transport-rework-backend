package app

import (
	"context"
	"log"
)

// Scanner discovers and stores video candidates from watched channels.
type Scanner struct {
	Store      *SQLiteStore
	Downloader Downloader
	JSRuntime  string
	RuleEngine *RuleEngine // Optional: run filtering after scan
	AutoFilter bool        // If true and RuleEngine is set, filter after scan
}

// ScanChannel fetches new videos from a channel and stores them as candidates.
func (s *Scanner) ScanChannel(ctx context.Context, channelID string, limit int) (int, error) {
	ch, err := s.Store.GetChannel(ctx, channelID)
	if err != nil {
		return 0, err
	}
	if ch == nil {
		return 0, nil
	}

	videos, err := s.Downloader.GetChannelVideosMetadata(ctx, ch.URL, limit, s.JSRuntime)
	if err != nil {
		return 0, err
	}

	count := 0
	for _, v := range videos {
		publishedAt := ParseYTDate(v.UploadDate)
		candidate := VideoCandidate{
			VideoID:         v.ID,
			ChannelID:       channelID,
			Title:           v.Title,
			Description:     v.Description,
			DurationSeconds: v.Duration,
			ViewCount:       v.ViewCount,
			LikeCount:       v.LikeCount,
			CommentCount:    v.CommentCount,
			PublishedAt:     publishedAt,
			ThumbnailURL:    v.Thumbnail,
			Tags:            v.Tags,
			Category:        FirstOrEmpty(v.Categories),
		}
		candidate.ViewVelocity = ComputeVelocity(candidate.ViewCount, candidate.PublishedAt)
		candidate.EngagementRate = ComputeEngagement(candidate.ViewCount, candidate.LikeCount, candidate.CommentCount)

		if err := s.Store.UpsertCandidate(ctx, candidate); err != nil {
			log.Printf("failed to store candidate %s: %v", v.ID, err)
			continue
		}
		count++
	}

	if err := s.Store.UpdateChannelScanned(ctx, channelID); err != nil {
		log.Printf("failed to update channel scan time for %s: %v", channelID, err)
	}

	// Optional: auto-filter newly discovered candidates
	if s.AutoFilter && s.RuleEngine != nil && count > 0 {
		candidates, err := s.Store.ListCandidatesByChannel(ctx, channelID, count)
		if err != nil {
			log.Printf("failed to fetch candidates for filtering: %v", err)
		} else {
			passed, rejected, err := s.RuleEngine.EvaluateBatch(ctx, candidates)
			if err != nil {
				log.Printf("filtering failed: %v", err)
			} else {
				log.Printf("Filtered: %d passed, %d rejected", len(passed), len(rejected))
			}
		}
	}

	return count, nil
}

// ScanAllActive scans all active channels.
func (s *Scanner) ScanAllActive(ctx context.Context, defaultLimit int) error {
	channels, err := s.Store.ListActiveChannels(ctx)
	if err != nil {
		return err
	}
	for _, ch := range channels {
		count, err := s.ScanChannel(ctx, ch.ChannelID, defaultLimit)
		if err != nil {
			log.Printf("scan %s failed: %v", ch.ChannelID, err)
			continue
		}
		log.Printf("scanned %s: discovered %d videos", ch.ChannelID, count)
	}
	return nil
}
