package app

import "time"

// ParseYTDate converts YYYYMMDD to time.Time.
func ParseYTDate(s string) *time.Time {
	if s == "" {
		return nil
	}
	t, err := time.Parse("20060102", s)
	if err != nil {
		return nil
	}
	return &t
}

// ComputeVelocity calculates views per hour since publish.
func ComputeVelocity(views int, publishedAt *time.Time) float64 {
	if publishedAt == nil || views == 0 {
		return 0
	}
	hours := time.Since(*publishedAt).Hours()
	if hours < 1 {
		hours = 1
	}
	return float64(views) / hours
}

// ComputeEngagement calculates (likes + comments) / views.
func ComputeEngagement(views, likes, comments int) float64 {
	if views == 0 {
		return 0
	}
	return float64(likes+comments) / float64(views)
}

// FirstOrEmpty returns the first element of a slice or an empty string.
func FirstOrEmpty(s []string) string {
	if len(s) == 0 {
		return ""
	}
	return s[0]
}
