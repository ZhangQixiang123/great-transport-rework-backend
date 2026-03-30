package app

import "testing"

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
