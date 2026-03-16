package app

import (
	"bytes"
	"context"
	"fmt"
	"log"
	"os/exec"
	"path/filepath"
	"runtime"
)

// SubtitlePipelineConfig holds settings for the subtitle generation pipeline.
type SubtitlePipelineConfig struct {
	PythonBinary string // path to python3 binary (default: "python3")
	WhisperScript string // path to whisper_transcribe.py
	WhisperModel string // whisper model size (default: "base")
	CookiePath   string // path to biliup cookies.json
}

// RunSubtitlePipeline runs the full subtitle pipeline for a video:
//  1. Transcribe audio → English SRT (via whisper subprocess)
//  2. Translate English SRT → Chinese SRT (via Google Translate)
//  3. Upload Chinese SRT as CC to Bilibili
func RunSubtitlePipeline(ctx context.Context, cfg SubtitlePipelineConfig, videoPath, bvid string) error {
	// Step 1: Transcribe
	log.Printf("subtitle-pipeline: transcribing %s", filepath.Base(videoPath))
	englishSRT, err := whisperTranscribe(ctx, cfg, videoPath)
	if err != nil {
		return fmt.Errorf("transcription failed: %w", err)
	}
	if englishSRT == "" {
		return fmt.Errorf("transcription produced empty result")
	}
	log.Printf("subtitle-pipeline: transcription complete, translating to Chinese")

	// Step 2: Translate
	chineseSRT, err := translateSRT(englishSRT)
	if err != nil {
		return fmt.Errorf("translation failed: %w", err)
	}
	if chineseSRT == "" {
		return fmt.Errorf("translation produced empty result")
	}
	log.Printf("subtitle-pipeline: translation complete, uploading CC to Bilibili")

	// Step 3: Upload to Bilibili
	if err := uploadSubtitleToBilibili(bvid, chineseSRT, cfg.CookiePath); err != nil {
		return fmt.Errorf("CC upload failed: %w", err)
	}

	log.Printf("subtitle-pipeline: done for %s (bvid=%s)", filepath.Base(videoPath), bvid)
	return nil
}

// whisperTranscribe calls the whisper_transcribe.py script and returns SRT content.
func whisperTranscribe(ctx context.Context, cfg SubtitlePipelineConfig, videoPath string) (string, error) {
	python := cfg.PythonBinary
	if python == "" {
		if runtime.GOOS == "windows" {
			python = "python"
		} else {
			python = "python3"
		}
	}

	script := cfg.WhisperScript
	if script == "" {
		// Default: look for script relative to the binary
		script = "scripts/whisper_transcribe.py"
	}

	model := cfg.WhisperModel
	if model == "" {
		model = "base"
	}

	args := []string{script, videoPath, "--model", model}
	cmd := exec.CommandContext(ctx, python, args...)

	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	log.Printf("subtitle-pipeline: running %s %v", python, args)
	if err := cmd.Run(); err != nil {
		log.Printf("subtitle-pipeline: whisper stderr: %s", stderr.String())
		return "", fmt.Errorf("whisper process failed: %w", err)
	}

	if stderr.Len() > 0 {
		log.Printf("subtitle-pipeline: whisper: %s", stderr.String())
	}

	return stdout.String(), nil
}
