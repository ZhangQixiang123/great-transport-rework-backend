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
