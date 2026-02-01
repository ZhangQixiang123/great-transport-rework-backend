package app

import (
	"context"
	"fmt"
)

// StatsService provides statistics queries for uploads.
type StatsService struct {
	store *SQLiteStore
}

// NewStatsService creates a new StatsService.
func NewStatsService(store *SQLiteStore) *StatsService {
	return &StatsService{store: store}
}

// GetOverallStats returns overall upload statistics.
func (s *StatsService) GetOverallStats(ctx context.Context) (*UploadStats, error) {
	return s.store.GetUploadStats(ctx)
}

// GetPerformanceSummary returns a summary of performance metrics.
func (s *StatsService) GetPerformanceSummary(ctx context.Context) (map[string]interface{}, error) {
	stats, err := s.store.GetUploadStats(ctx)
	if err != nil {
		return nil, err
	}

	summary := map[string]interface{}{
		"total_uploads":            stats.TotalUploads,
		"uploads_with_bvid":        stats.UploadsWithBvid,
		"uploads_with_performance": stats.UploadsWithPerformance,
		"uploads_by_label":         stats.UploadsByLabel,
		"avg_views":                stats.AvgViews,
		"avg_likes":                stats.AvgLikes,
		"avg_coins":                stats.AvgCoins,
		"avg_engagement_rate":      stats.AvgEngagementRate,
	}

	return summary, nil
}

// GetRecentUploads returns recent uploads with their performance.
func (s *StatsService) GetRecentUploads(ctx context.Context, limit int) ([]UploadWithPerformance, error) {
	return s.store.ListRecentUploadsWithPerformance(ctx, limit)
}

// GetUploadDetails returns detailed information about a specific upload.
func (s *StatsService) GetUploadDetails(ctx context.Context, videoID string) (*UploadDetails, error) {
	upload, err := s.store.GetUpload(ctx, videoID)
	if err != nil {
		return nil, err
	}
	if upload == nil {
		return nil, fmt.Errorf("upload not found: %s", videoID)
	}

	performance, err := s.store.GetUploadPerformance(ctx, videoID)
	if err != nil {
		return nil, err
	}

	outcome, err := s.store.GetUploadOutcome(ctx, videoID)
	if err != nil {
		return nil, err
	}

	return &UploadDetails{
		Upload:      *upload,
		Performance: performance,
		Outcome:     outcome,
	}, nil
}

// UploadDetails contains detailed information about an upload.
type UploadDetails struct {
	Upload      Upload
	Performance []UploadPerformance
	Outcome     *UploadOutcome
}

// GetLabelDistribution returns the distribution of success labels.
func (s *StatsService) GetLabelDistribution(ctx context.Context) (map[string]int, error) {
	stats, err := s.store.GetUploadStats(ctx)
	if err != nil {
		return nil, err
	}
	return stats.UploadsByLabel, nil
}
