package main

import (
	"errors"
	"flag"
	"io"
	"reflect"
	"strings"
	"testing"
)

func TestParseFlagsFrom(t *testing.T) {
	tests := []struct {
		name    string
		args    []string
		want    config
		wantErr string
	}{
		{
			name: "video defaults",
			args: []string{"--video-id", "abc123"},
			want: config{
				videoID:      "abc123",
				platform:     "bilibili",
				outputDir:    "downloads",
				dbPath:       "metadata.db",
				httpAddr:     "",
				limit:        5,
				sleepSeconds: 5,
				jsRuntime:    "auto",
				format:       "auto",
			},
		},
		{
			name: "channel custom",
			args: []string{"--channel-id", "UC123", "--limit", "3", "--platform", "tiktok", "--output", "out", "--sleep-seconds", "7"},
			want: config{
				channelID:    "UC123",
				platform:     "tiktok",
				outputDir:    "out",
				dbPath:       "metadata.db",
				httpAddr:     "",
				limit:        3,
				sleepSeconds: 7,
				jsRuntime:    "auto",
				format:       "auto",
			},
		},
		{
			name:    "missing id",
			wantErr: "provide either --channel-id or --video-id",
		},
		{
			name: "http server without ids",
			args: []string{"--http-addr", ":8080"},
			want: config{
				platform:     "bilibili",
				outputDir:    "downloads",
				dbPath:       "metadata.db",
				httpAddr:     ":8080",
				limit:        5,
				sleepSeconds: 5,
				jsRuntime:    "auto",
				format:       "auto",
			},
		},
		{
			name:    "both ids",
			args:    []string{"--video-id", "vid", "--channel-id", "chan"},
			wantErr: "provide only one of --channel-id or --video-id",
		},
		{
			name:    "channel limit",
			args:    []string{"--channel-id", "chan", "--limit", "0"},
			wantErr: "--limit must be > 0 for channel downloads",
		},
		{
			name:    "negative sleep",
			args:    []string{"--video-id", "vid", "--sleep-seconds", "-1"},
			wantErr: "--sleep-seconds must be >= 0",
		},
		{
			name:    "bad platform",
			args:    []string{"--video-id", "vid", "--platform", "myspace"},
			wantErr: "--platform must be bilibili or tiktok",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			fs := flag.NewFlagSet(tt.name, flag.ContinueOnError)
			fs.SetOutput(io.Discard)
			got, err := parseFlagsFrom(fs, tt.args)
			if tt.wantErr != "" {
				if err == nil || err.Error() != tt.wantErr {
					t.Fatalf("expected error %q, got %v", tt.wantErr, err)
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if !reflect.DeepEqual(got, tt.want) {
				t.Fatalf("unexpected config: %#v", got)
			}
		})
	}
}

func TestChannelURL(t *testing.T) {
	if got := channelURL("https://example.com/channel/abc"); got != "https://example.com/channel/abc" {
		t.Fatalf("expected passthrough for URL, got %s", got)
	}
	if got := channelURL("UCxyz"); got != "https://www.youtube.com/channel/UCxyz/videos" {
		t.Fatalf("unexpected channel URL: %s", got)
	}
}

func TestVideoURL(t *testing.T) {
	if got := videoURL("https://youtu.be/abc"); got != "https://youtu.be/abc" {
		t.Fatalf("expected passthrough for URL, got %s", got)
	}
	if got := videoURL("123"); got != "https://www.youtube.com/watch?v=123" {
		t.Fatalf("unexpected video URL: %s", got)
	}
}

func TestLooksLikeURL(t *testing.T) {
	cases := []struct {
		input string
		want  bool
	}{
		{"https://example.com", true},
		{"http://example.com", true},
		{"ftp://example.com", false},
		{"example.com", false},
	}
	for _, c := range cases {
		if got := looksLikeURL(c.input); got != c.want {
			t.Fatalf("looksLikeURL(%q)=%v, want %v", c.input, got, c.want)
		}
	}
}

func TestResolveJSRuntime(t *testing.T) {
	restore := lookPath
	t.Cleanup(func() { lookPath = restore })

	tests := []struct {
		name      string
		preferred string
		available map[string]bool
		want      string
		expectErr bool
	}{
		{"auto picks node", "auto", map[string]bool{"node": true}, "node", false},
		{"fallback to deno", "bun, deno", map[string]bool{"deno": true}, "deno", false},
		{"explicit node missing", "node", map[string]bool{}, "", true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			lookPath = func(name string) (string, error) {
				if tt.available[name] {
					return "/usr/bin/" + name, nil
				}
				return "", errors.New("not found")
			}
			got, err := resolveJSRuntime(tt.preferred)
			if tt.expectErr {
				if err == nil {
					t.Fatal("expected error, got nil")
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if got != tt.want {
				t.Fatalf("runtime=%s, want %s", got, tt.want)
			}
		})
	}
}

func TestDetermineFormat(t *testing.T) {
	restore := lookPath
	t.Cleanup(func() { lookPath = restore })

	tests := []struct {
		name      string
		input     string
		available map[string]bool
		wantFmt   string
		wantWarn  string
	}{
		{"auto with ffmpeg", "auto", map[string]bool{"ffmpeg": true}, "bv*+ba/b", ""},
		{"auto no ffmpeg", "auto", map[string]bool{}, "", "falling back"},
		{"custom without merge", "bestaudio", map[string]bool{}, "bestaudio", ""},
		{"custom with merge no ffmpeg", "bestvideo+bestaudio", map[string]bool{}, "bestvideo+bestaudio", "ffmpeg not found"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			lookPath = func(name string) (string, error) {
				if tt.available[name] {
					return "/usr/bin/" + name, nil
				}
				return "", errors.New("not found")
			}
			gotFmt, gotWarn := determineFormat(tt.input)
			if gotFmt != tt.wantFmt {
				t.Fatalf("format=%s, want %s", gotFmt, tt.wantFmt)
			}
			if tt.wantWarn == "" && gotWarn != "" {
				t.Fatalf("unexpected warning: %s", gotWarn)
			}
			if tt.wantWarn != "" && !strings.Contains(gotWarn, tt.wantWarn) {
				t.Fatalf("warning %q does not contain %q", gotWarn, tt.wantWarn)
			}
		})
	}
}
