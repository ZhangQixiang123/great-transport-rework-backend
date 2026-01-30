package app

import (
	"testing"
	"time"
)

func TestParseYTDate(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"20240115", "2024-01-15"},
		{"20231225", "2023-12-25"},
		{"", ""},
		{"invalid", ""},
	}
	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			got := ParseYTDate(tt.input)
			if tt.want == "" {
				if got != nil {
					t.Fatalf("ParseYTDate(%q) = %v, want nil", tt.input, got)
				}
				return
			}
			if got == nil {
				t.Fatalf("ParseYTDate(%q) = nil, want %s", tt.input, tt.want)
			}
			gotStr := got.Format("2006-01-02")
			if gotStr != tt.want {
				t.Fatalf("ParseYTDate(%q) = %s, want %s", tt.input, gotStr, tt.want)
			}
		})
	}
}

func TestComputeVelocity(t *testing.T) {
	// Test with nil publishedAt
	if v := ComputeVelocity(1000, nil); v != 0 {
		t.Fatalf("ComputeVelocity with nil date = %f, want 0", v)
	}

	// Test with zero views
	now := time.Now()
	if v := ComputeVelocity(0, &now); v != 0 {
		t.Fatalf("ComputeVelocity with zero views = %f, want 0", v)
	}

	// Test with valid inputs
	twoHoursAgo := time.Now().Add(-2 * time.Hour)
	v := ComputeVelocity(1000, &twoHoursAgo)
	if v < 450 || v > 550 {
		t.Fatalf("ComputeVelocity(1000, 2 hours ago) = %f, expected ~500", v)
	}
}

func TestComputeEngagement(t *testing.T) {
	tests := []struct {
		views, likes, comments int
		want                   float64
	}{
		{0, 10, 5, 0},
		{1000, 50, 10, 0.06},
		{100, 0, 0, 0},
	}
	for _, tt := range tests {
		got := ComputeEngagement(tt.views, tt.likes, tt.comments)
		if got != tt.want {
			t.Fatalf("ComputeEngagement(%d, %d, %d) = %f, want %f",
				tt.views, tt.likes, tt.comments, got, tt.want)
		}
	}
}

func TestFirstOrEmpty(t *testing.T) {
	tests := []struct {
		input []string
		want  string
	}{
		{nil, ""},
		{[]string{}, ""},
		{[]string{"first"}, "first"},
		{[]string{"first", "second"}, "first"},
	}
	for _, tt := range tests {
		got := FirstOrEmpty(tt.input)
		if got != tt.want {
			t.Fatalf("FirstOrEmpty(%v) = %q, want %q", tt.input, got, tt.want)
		}
	}
}
