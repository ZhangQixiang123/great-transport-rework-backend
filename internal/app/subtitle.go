package app

import (
	"context"
	"fmt"
	"log"
	"os"
	"os/exec"
)

// SubtitleConfig holds settings for whisper_autosrt subtitle generation.
type SubtitleConfig struct {
	Binary   string // path to whisper_autosrt binary
	SrcLang  string // source language (e.g. "en")
	DstLang  string // destination/translation language (e.g. "zh")
	Model    string // whisper model size (e.g. "medium")
	EmbedSrc bool   // embed source-language subtitles into video
	EmbedDst bool   // embed destination-language subtitles into video
}

// SubtitleGenerator generates and embeds subtitles using whisper_autosrt.
type SubtitleGenerator struct {
	config SubtitleConfig
}

// NewSubtitleGenerator creates a SubtitleGenerator from config.
func NewSubtitleGenerator(cfg SubtitleConfig) *SubtitleGenerator {
	return &SubtitleGenerator{config: cfg}
}

// Generate runs whisper_autosrt on the given video file.
// It embeds subtitles according to the config flags.
func (g *SubtitleGenerator) Generate(ctx context.Context, videoPath string) error {
	binary := g.config.Binary
	if binary == "" {
		binary = "whisper_autosrt"
	}

	args := []string{
		"-S", g.config.SrcLang,
		"-D", g.config.DstLang,
	}

	if g.config.EmbedSrc {
		args = append(args, "-es")
	}
	if g.config.EmbedDst {
		args = append(args, "-ed")
	}

	if g.config.Model != "" {
		args = append(args, "--whisper-model", g.config.Model)
	}

	args = append(args, videoPath)

	log.Printf("subtitle: running %s %v", binary, args)

	cmd := exec.CommandContext(ctx, binary, args...)
	cmd.Stdout = newPrefixedLogger("subtitle")
	cmd.Stderr = newPrefixedLogger("subtitle")

	// Set PYTHONIOENCODING for Windows compatibility.
	cmd.Env = append(os.Environ(), "PYTHONIOENCODING=utf-8")

	if err := cmd.Run(); err != nil {
		return fmt.Errorf("whisper_autosrt failed: %w", err)
	}

	return nil
}
