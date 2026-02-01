package app

import (
	"testing"
)

func TestParseBvidFromOutput_ValidBvid(t *testing.T) {
	tests := []struct {
		name   string
		output string
		want   string
	}{
		{
			name:   "bvid with colon",
			output: "Upload complete. bvid: BV1AB411c7XY",
			want:   "BV1AB411c7XY",
		},
		{
			name:   "bvid with equals",
			output: "Success! bvid=BV1xx411x7xx",
			want:   "BV1xx411x7xx",
		},
		{
			name:   "bvid in JSON format double quotes",
			output: `{"code":0,"data":{"bvid":"BV1Qx411B7kk","aid":12345}}`,
			want:   "BV1Qx411B7kk",
		},
		{
			name:   "bvid in JSON format single quotes",
			output: `{'bvid': 'BV1Yx411Y7zz'}`,
			want:   "BV1Yx411Y7zz",
		},
		{
			name:   "bvid in URL",
			output: "Video URL: https://www.bilibili.com/video/BV1Mx411M7kM",
			want:   "BV1Mx411M7kM",
		},
		{
			name:   "standalone bvid",
			output: "Upload finished: BV1Nx411N8nN video is processing",
			want:   "BV1Nx411N8nN",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := parseBvidFromOutput(tt.output)
			if got != tt.want {
				t.Errorf("parseBvidFromOutput() = %q, want %q", got, tt.want)
			}
		})
	}
}

func TestParseBvidFromOutput_NoBvid(t *testing.T) {
	tests := []struct {
		name   string
		output string
	}{
		{
			name:   "empty output",
			output: "",
		},
		{
			name:   "no bvid in output",
			output: "Upload failed: network error",
		},
		{
			name:   "partial bvid-like string",
			output: "BV123 is not a valid video",
		},
		{
			name:   "av number instead of bvid",
			output: "Video av12345678 uploaded",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := parseBvidFromOutput(tt.output)
			if got != "" {
				t.Errorf("parseBvidFromOutput() = %q, want empty string", got)
			}
		})
	}
}

func TestParseBvidFromOutput_VariousFormats(t *testing.T) {
	tests := []struct {
		name   string
		output string
		want   string
	}{
		{
			name:   "lowercase bvid keyword",
			output: "bvid: BV1Zx411Z9zZ",
			want:   "BV1Zx411Z9zZ",
		},
		{
			name:   "uppercase BVID keyword",
			output: "BVID: BV1Wx411W8wW",
			want:   "BV1Wx411W8wW",
		},
		{
			name:   "mixed case BvId keyword",
			output: "BvId=BV1Vx411V7vV",
			want:   "BV1Vx411V7vV",
		},
		{
			name:   "bvid with spaces",
			output: "bvid :  BV1Ux411U6uU",
			want:   "BV1Ux411U6uU",
		},
		{
			name:   "multiple bvids returns first",
			output: "bvid: BV1Tx411T5tT and another BV1Sx411S4sS",
			want:   "BV1Tx411T5tT",
		},
		{
			name:   "bvid in multiline output",
			output: "Starting upload...\nProgress: 100%\nbvid: BV1Rx411R3rR\nDone!",
			want:   "BV1Rx411R3rR",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := parseBvidFromOutput(tt.output)
			if got != tt.want {
				t.Errorf("parseBvidFromOutput() = %q, want %q", got, tt.want)
			}
		})
	}
}

func TestUploadResult_Structure(t *testing.T) {
	// Test that UploadResult can hold a bvid
	result := UploadResult{
		BilibiliBvid: "BV1Px411P2pP",
	}
	if result.BilibiliBvid != "BV1Px411P2pP" {
		t.Errorf("UploadResult.BilibiliBvid = %q, want %q", result.BilibiliBvid, "BV1Px411P2pP")
	}
}

func TestNewBiliupUploader_DefaultLimit(t *testing.T) {
	// Test that default limit is set when not provided
	uploader := NewBiliupUploader(BiliupUploaderOptions{})
	if uploader.opts.Limit != 3 {
		t.Errorf("Default limit = %d, want 3", uploader.opts.Limit)
	}
}

func TestNewBiliupUploader_CustomLimit(t *testing.T) {
	// Test that custom limit is preserved
	uploader := NewBiliupUploader(BiliupUploaderOptions{Limit: 5})
	if uploader.opts.Limit != 5 {
		t.Errorf("Custom limit = %d, want 5", uploader.opts.Limit)
	}
}

func TestBiliupUploader_BuildMetadata(t *testing.T) {
	uploader := NewBiliupUploader(BiliupUploaderOptions{
		TitlePrefix: "[搬运] ",
		Description: "Test description",
		Dynamic:     "Test dynamic",
		Tags:        []string{"tag1", "tag2", ""},
	})

	meta := uploader.buildMetadata("/path/to/video.mp4")

	if meta.Title != "[搬运] video" {
		t.Errorf("Title = %q, want %q", meta.Title, "[搬运] video")
	}
	if meta.Description != "Test description" {
		t.Errorf("Description = %q, want %q", meta.Description, "Test description")
	}
	if meta.Dynamic != "Test dynamic" {
		t.Errorf("Dynamic = %q, want %q", meta.Dynamic, "Test dynamic")
	}
	if meta.Tag != "tag1,tag2" {
		t.Errorf("Tag = %q, want %q", meta.Tag, "tag1,tag2")
	}
}
