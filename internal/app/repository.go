package app

import "time"

// Channel represents a YouTube channel to monitor for new videos.
type Channel struct {
	ChannelID          string
	Name               string
	URL                string
	SubscriberCount    int
	VideoCount         int
	LastScannedAt      *time.Time
	ScanFrequencyHours int
	IsActive           bool
	CreatedAt          time.Time
}

// VideoCandidate represents a discovered video that may be selected for transfer.
type VideoCandidate struct {
	VideoID         string
	ChannelID       string
	Title           string
	Description     string
	DurationSeconds int
	ViewCount       int
	LikeCount       int
	CommentCount    int
	PublishedAt     *time.Time
	DiscoveredAt    time.Time
	ThumbnailURL    string
	Tags            []string
	Category        string
	Language        string
	ViewVelocity    float64
	EngagementRate  float64
}

// FilterRule represents a configurable rule for filtering video candidates.
type FilterRule struct {
	ID        int
	RuleName  string
	RuleType  string // min, max, blocklist, allowlist, regex, age_days
	Field     string // view_count, duration_seconds, category, title, etc.
	Value     string // JSON-encoded value
	IsActive  bool
	Priority  int
	CreatedAt time.Time
}

// RuleDecision records the result of rule evaluation for a video candidate.
type RuleDecision struct {
	ID             int
	VideoID        string
	RulePassed     bool
	RejectRuleName string
	RejectReason   string
	EvaluatedAt    time.Time
}

// RejectedCandidate represents a video that was rejected by rules (for listing).
type RejectedCandidate struct {
	VideoID        string
	Title          string
	ViewCount      int
	PublishedAt    *time.Time
	RejectRuleName string
	RejectReason   string
	EvaluatedAt    time.Time
}

// Upload represents a video that has been uploaded to Bilibili.
type Upload struct {
	VideoID      string
	ChannelID    string
	BilibiliBvid string
	UploadedAt   time.Time
}

// UploadPerformance represents performance metrics for an uploaded video at a specific checkpoint.
type UploadPerformance struct {
	ID              int
	UploadID        string // YouTube video ID (FK to uploads)
	CheckpointHours int    // 1, 6, 24, 48, 168 (7d), 720 (30d)
	RecordedAt      time.Time
	Views           int
	Likes           int
	Coins           int
	Favorites       int
	Shares          int
	Danmaku         int
	Comments        int
	ViewVelocity    float64 // views per hour since upload
	EngagementRate  float64 // (likes + coins + favorites) / views
}

// UploadOutcome represents the final success label for an uploaded video.
type UploadOutcome struct {
	ID                  int
	UploadID            string // YouTube video ID (FK to uploads)
	Label               string // viral, successful, standard, failed
	LabeledAt           time.Time
	FinalViews          int
	FinalEngagementRate float64
	FinalCoins          int
}

// UploadStats holds aggregate statistics about uploads.
type UploadStats struct {
	TotalUploads           int
	UploadsWithBvid        int
	UploadsWithPerformance int
	UploadsByLabel         map[string]int
	AvgViews               float64
	AvgLikes               float64
	AvgCoins               float64
	AvgEngagementRate      float64
}

// UploadWithPerformance combines upload info with latest performance data.
type UploadWithPerformance struct {
	VideoID          string
	ChannelID        string
	BilibiliBvid     string
	UploadedAt       time.Time
	Views            int
	Likes            int
	Coins            int
	EngagementRate   float64
	LatestCheckpoint int
	Label            string
}
