package app

import (
	"context"
	"testing"
	"time"
)

func TestSubtitleGeneratorBuildArgs(t *testing.T) {
	// Test that NewSubtitleGenerator creates without error.
	cfg := SubtitleConfig{
		Binary:   "whisper_autosrt",
		SrcLang:  "en",
		DstLang:  "zh",
		Model:    "medium",
		EmbedSrc: false,
		EmbedDst: true,
	}
	gen := NewSubtitleGenerator(cfg)
	if gen == nil {
		t.Fatal("NewSubtitleGenerator returned nil")
	}
	if gen.config.SrcLang != "en" {
		t.Errorf("SrcLang: got %q, want %q", gen.config.SrcLang, "en")
	}
	if gen.config.DstLang != "zh" {
		t.Errorf("DstLang: got %q, want %q", gen.config.DstLang, "zh")
	}
	if gen.config.EmbedSrc != false {
		t.Error("EmbedSrc should be false")
	}
	if gen.config.EmbedDst != true {
		t.Error("EmbedDst should be true")
	}
}

func TestSubtitleGeneratorMissingBinary(t *testing.T) {
	gen := NewSubtitleGenerator(SubtitleConfig{
		Binary:  "nonexistent_binary_xyz",
		SrcLang: "en",
		DstLang: "zh",
	})

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	err := gen.Generate(ctx, "fake.mp4")
	if err == nil {
		t.Fatal("expected error for missing binary")
	}
}
