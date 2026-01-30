package main

import (
	"errors"
	"flag"
	"io"
	"reflect"
	"strings"
	"testing"

	"great_transport/internal/app"
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
				videoID:        "abc123",
				platform:       "bilibili",
				outputDir:      "downloads",
				dbPath:         "metadata.db",
				httpAddr:       "",
				limit:          5,
				sleepSeconds:   5,
				jsRuntime:      "auto",
				format:         "auto",
				biliupBinary:   "biliup",
				biliupCookie:   "cookies.json",
				biliupLine:     "",
				biliupLimit:    3,
				biliupTags:     "",
				biliupTitle:    "",
				biliupDesc:     "Uploaded via yt-transfer",
				biliupDynamic:  "",
				candidateLimit: 20,
			},
		},
		{
			name: "channel custom",
			args: []string{"--channel-id", "UC123", "--limit", "3", "--platform", "tiktok", "--output", "out", "--sleep-seconds", "7"},
			want: config{
				channelID:      "UC123",
				platform:       "tiktok",
				outputDir:      "out",
				dbPath:         "metadata.db",
				httpAddr:       "",
				limit:          3,
				sleepSeconds:   7,
				jsRuntime:      "auto",
				format:         "auto",
				biliupBinary:   "biliup",
				biliupCookie:   "cookies.json",
				biliupLine:     "",
				biliupLimit:    3,
				biliupTags:     "",
				biliupTitle:    "",
				biliupDesc:     "Uploaded via yt-transfer",
				biliupDynamic:  "",
				candidateLimit: 20,
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
				platform:       "bilibili",
				outputDir:      "downloads",
				dbPath:         "metadata.db",
				httpAddr:       ":8080",
				limit:          5,
				sleepSeconds:   5,
				jsRuntime:      "auto",
				format:         "auto",
				biliupBinary:   "biliup",
				biliupCookie:   "cookies.json",
				biliupLine:     "",
				biliupLimit:    3,
				biliupTags:     "",
				biliupTitle:    "",
				biliupDesc:     "Uploaded via yt-transfer",
				biliupDynamic:  "",
				candidateLimit: 20,
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
		{
			name: "add channel mode",
			args: []string{"--add-channel", "https://www.youtube.com/@TestChannel"},
			want: config{
				platform:       "bilibili",
				outputDir:      "downloads",
				dbPath:         "metadata.db",
				limit:          5,
				sleepSeconds:   5,
				jsRuntime:      "auto",
				format:         "auto",
				biliupBinary:   "biliup",
				biliupCookie:   "cookies.json",
				biliupLimit:    3,
				biliupDesc:     "Uploaded via yt-transfer",
				addChannel:     "https://www.youtube.com/@TestChannel",
				candidateLimit: 20,
			},
		},
		{
			name: "scan mode",
			args: []string{"--scan"},
			want: config{
				platform:       "bilibili",
				outputDir:      "downloads",
				dbPath:         "metadata.db",
				limit:          5,
				sleepSeconds:   5,
				jsRuntime:      "auto",
				format:         "auto",
				biliupBinary:   "biliup",
				biliupCookie:   "cookies.json",
				biliupLimit:    3,
				biliupDesc:     "Uploaded via yt-transfer",
				scan:           true,
				candidateLimit: 20,
			},
		},
		{
			name: "list candidates mode",
			args: []string{"--list-candidates", "--candidate-limit", "50"},
			want: config{
				platform:       "bilibili",
				outputDir:      "downloads",
				dbPath:         "metadata.db",
				limit:          5,
				sleepSeconds:   5,
				jsRuntime:      "auto",
				format:         "auto",
				biliupBinary:   "biliup",
				biliupCookie:   "cookies.json",
				biliupLimit:    3,
				biliupDesc:     "Uploaded via yt-transfer",
				listCandidates: true,
				candidateLimit: 50,
			},
		},
		{
			name: "list rules mode",
			args: []string{"--list-rules"},
			want: config{
				platform:       "bilibili",
				outputDir:      "downloads",
				dbPath:         "metadata.db",
				limit:          5,
				sleepSeconds:   5,
				jsRuntime:      "auto",
				format:         "auto",
				biliupBinary:   "biliup",
				biliupCookie:   "cookies.json",
				biliupLimit:    3,
				biliupDesc:     "Uploaded via yt-transfer",
				listRules:      true,
				candidateLimit: 20,
			},
		},
		{
			name: "set rule mode",
			args: []string{"--set-rule", "min_views=5000"},
			want: config{
				platform:       "bilibili",
				outputDir:      "downloads",
				dbPath:         "metadata.db",
				limit:          5,
				sleepSeconds:   5,
				jsRuntime:      "auto",
				format:         "auto",
				biliupBinary:   "biliup",
				biliupCookie:   "cookies.json",
				biliupLimit:    3,
				biliupDesc:     "Uploaded via yt-transfer",
				setRule:        "min_views=5000",
				candidateLimit: 20,
			},
		},
		{
			name: "add rule mode",
			args: []string{"--add-rule", `{"name":"test","type":"min","field":"view_count","value":"100"}`},
			want: config{
				platform:       "bilibili",
				outputDir:      "downloads",
				dbPath:         "metadata.db",
				limit:          5,
				sleepSeconds:   5,
				jsRuntime:      "auto",
				format:         "auto",
				biliupBinary:   "biliup",
				biliupCookie:   "cookies.json",
				biliupLimit:    3,
				biliupDesc:     "Uploaded via yt-transfer",
				addRule:        `{"name":"test","type":"min","field":"view_count","value":"100"}`,
				candidateLimit: 20,
			},
		},
		{
			name: "remove rule mode",
			args: []string{"--remove-rule", "test_rule"},
			want: config{
				platform:       "bilibili",
				outputDir:      "downloads",
				dbPath:         "metadata.db",
				limit:          5,
				sleepSeconds:   5,
				jsRuntime:      "auto",
				format:         "auto",
				biliupBinary:   "biliup",
				biliupCookie:   "cookies.json",
				biliupLimit:    3,
				biliupDesc:     "Uploaded via yt-transfer",
				removeRule:     "test_rule",
				candidateLimit: 20,
			},
		},
		{
			name: "filter mode",
			args: []string{"--filter", "--limit", "100"},
			want: config{
				platform:         "bilibili",
				outputDir:        "downloads",
				dbPath:           "metadata.db",
				limit:            100,
				sleepSeconds:     5,
				jsRuntime:        "auto",
				format:           "auto",
				biliupBinary:     "biliup",
				biliupCookie:     "cookies.json",
				biliupLimit:      3,
				biliupDesc:       "Uploaded via yt-transfer",
				filterCandidates: true,
				candidateLimit:   20,
			},
		},
		{
			name: "list filtered mode",
			args: []string{"--list-filtered"},
			want: config{
				platform:       "bilibili",
				outputDir:      "downloads",
				dbPath:         "metadata.db",
				limit:          5,
				sleepSeconds:   5,
				jsRuntime:      "auto",
				format:         "auto",
				biliupBinary:   "biliup",
				biliupCookie:   "cookies.json",
				biliupLimit:    3,
				biliupDesc:     "Uploaded via yt-transfer",
				listFiltered:   true,
				candidateLimit: 20,
			},
		},
		{
			name: "list rejected mode",
			args: []string{"--list-rejected"},
			want: config{
				platform:       "bilibili",
				outputDir:      "downloads",
				dbPath:         "metadata.db",
				limit:          5,
				sleepSeconds:   5,
				jsRuntime:      "auto",
				format:         "auto",
				biliupBinary:   "biliup",
				biliupCookie:   "cookies.json",
				biliupLimit:    3,
				biliupDesc:     "Uploaded via yt-transfer",
				listRejected:   true,
				candidateLimit: 20,
			},
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

func TestResolveJSRuntime(t *testing.T) {
	restore := app.LookPath
	t.Cleanup(func() { app.LookPath = restore })

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
			app.LookPath = func(name string) (string, error) {
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

func TestExtractChannelID(t *testing.T) {
	tests := []struct {
		input string
		want  string
	}{
		{"https://www.youtube.com/channel/UC123456", "UC123456"},
		{"https://www.youtube.com/@SomeChannel", "@SomeChannel"},
		{"https://www.youtube.com/@SomeChannel/videos", "@SomeChannel"},
		{"UC123456", "UC123456"},
		{"@SomeHandle", "@SomeHandle"},
	}
	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			got := extractChannelID(tt.input)
			if got != tt.want {
				t.Fatalf("extractChannelID(%q) = %q, want %q", tt.input, got, tt.want)
			}
		})
	}
}

func TestTruncate(t *testing.T) {
	tests := []struct {
		input  string
		maxLen int
		want   string
	}{
		{"short", 10, "short"},
		{"exactly ten", 11, "exactly ten"},
		{"this is a longer string", 10, "this is..."},
	}
	for _, tt := range tests {
		t.Run(tt.input, func(t *testing.T) {
			got := truncate(tt.input, tt.maxLen)
			if got != tt.want {
				t.Fatalf("truncate(%q, %d) = %q, want %q", tt.input, tt.maxLen, got, tt.want)
			}
		})
	}
}

func TestDetermineFormat(t *testing.T) {
	restore := app.LookPath
	t.Cleanup(func() { app.LookPath = restore })

	tests := []struct {
		name      string
		input     string
		available map[string]bool
		wantFmt   string
		wantWarn  string
	}{
		{"auto with ffmpeg", "auto", map[string]bool{"ffmpeg": true}, "bv*[ext=mp4]+ba[ext=m4a]/bv*[ext=mp4]/b[ext=mp4]/bv*+ba/b", ""},
		{"auto no ffmpeg", "auto", map[string]bool{}, "b[ext=mp4]/b", "falling back"},
		{"custom without merge", "bestaudio", map[string]bool{}, "bestaudio", ""},
		{"custom with merge no ffmpeg", "bestvideo+bestaudio", map[string]bool{}, "bestvideo+bestaudio", "ffmpeg not found"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			app.LookPath = func(name string) (string, error) {
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
