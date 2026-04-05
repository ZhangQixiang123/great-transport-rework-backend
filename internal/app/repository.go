package app

import "time"

// Upload represents a video that has been uploaded to Bilibili.
type Upload struct {
	VideoID      string
	ChannelID    string
	BilibiliBvid string
	UploadedAt   time.Time
}

// UploadJob represents a video upload job submitted via the HTTP API.
type UploadJob struct {
	ID             int64
	VideoID        string
	Status         string // pending, downloading, uploading, completed, failed
	Title          string
	Description    string
	Tags           string
	BilibiliBvid   string
	DownloadFiles  string // JSON array of downloaded file paths
	SubtitleStatus string // pending, generating, completed, failed
	ErrorMessage   string
	PersonaID      string
	StrategyName   string
	CreatedAt      time.Time
	UpdatedAt      time.Time
}
