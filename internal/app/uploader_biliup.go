package app

import (
	"bytes"
	"fmt"
	"io"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
)

type BiliupUploaderOptions struct {
	Binary      string
	CookiePath  string
	Line        string
	Limit       int
	TitlePrefix string
	Description string
	Dynamic     string
	Tags        []string
}

type BiliupUploader struct {
	opts BiliupUploaderOptions
}

func NewBiliupUploader(opts BiliupUploaderOptions) *BiliupUploader {
	if opts.Limit <= 0 {
		opts.Limit = 3
	}
	return &BiliupUploader{opts: opts}
}

// UploadResult contains the result of a successful upload.
type UploadResult struct {
	BilibiliBvid string // Bilibili video ID (e.g., BV1xx411x7xx)
}

// Upload uploads a video to Bilibili and returns the result including bvid.
// This method is kept for backward compatibility.
func (u *BiliupUploader) Upload(path string) error {
	_, err := u.UploadWithResult(path)
	return err
}

// UploadWithResult uploads a video and returns the upload result including bvid.
func (u *BiliupUploader) UploadWithResult(path string) (*UploadResult, error) {
	binary := u.opts.Binary
	if binary == "" {
		binary = "biliup"
	}
	if _, err := LookPath(binary); err != nil {
		return nil, fmt.Errorf("biliup binary %q not found in PATH; install it from github.com/biliup/biliup or set --biliup-binary", binary)
	}

	cookie := u.opts.CookiePath
	if cookie == "" {
		cookie = "cookies.json"
	}
	if err := ensureCookieExists(cookie); err != nil {
		return nil, err
	}

	meta := u.buildMetadata(path)

	args := []string{"--user-cookie", cookie, "upload", "--limit", strconv.Itoa(u.opts.Limit)}
	if u.opts.Line != "" {
		args = append(args, "--line", u.opts.Line)
	}
	args = append(args, "--title", meta.Title)
	if meta.Description != "" {
		args = append(args, "--desc", meta.Description)
	}
	if meta.Dynamic != "" {
		args = append(args, "--dynamic", meta.Dynamic)
	}
	if meta.Tag != "" {
		args = append(args, "--tag", meta.Tag)
	}
	args = append(args, path)
	log.Println("Uploading the video at path:" + path)

	cmd := exec.Command(binary, args...)

	// Capture stdout to parse bvid
	var stdoutBuf, stderrBuf bytes.Buffer
	stdoutWriter := io.MultiWriter(&stdoutBuf, newPrefixedLogger("biliup"))
	stderrWriter := io.MultiWriter(&stderrBuf, newPrefixedLogger("biliup"))
	cmd.Stdout = stdoutWriter
	cmd.Stderr = stderrWriter

	if err := cmd.Run(); err != nil {
		return nil, fmt.Errorf("biliup upload failed: %w", err)
	}

	// Parse bvid from output
	result := &UploadResult{}
	output := stdoutBuf.String() + stderrBuf.String()
	result.BilibiliBvid = parseBvidFromOutput(output)

	if result.BilibiliBvid != "" {
		log.Printf("Upload successful, Bilibili bvid: %s", result.BilibiliBvid)
	} else {
		log.Println("Upload successful, but could not parse bvid from output")
	}

	return result, nil
}

// parseBvidFromOutput extracts the Bilibili video ID from biliup output.
// biliup typically outputs lines like: "bvid: BV1xx411x7xx" or contains the bvid in the response.
func parseBvidFromOutput(output string) string {
	// Pattern 1: Direct bvid output (e.g., "bvid: BV1xx411x7xx" or "bvid=BV1xx411x7xx")
	bvidPatterns := []*regexp.Regexp{
		regexp.MustCompile(`[Bb][Vv][Ii][Dd][:\s=]+([Bb][Vv][0-9a-zA-Z]+)`),
		regexp.MustCompile(`"bvid"\s*:\s*"([Bb][Vv][0-9a-zA-Z]+)"`),
		regexp.MustCompile(`'bvid'\s*:\s*'([Bb][Vv][0-9a-zA-Z]+)'`),
		// Pattern for URL containing bvid
		regexp.MustCompile(`bilibili\.com/video/([Bb][Vv][0-9a-zA-Z]+)`),
		// Standalone BV pattern (less reliable, used as fallback)
		regexp.MustCompile(`\b([Bb][Vv]1[0-9a-zA-Z]{9})\b`),
	}

	for _, pattern := range bvidPatterns {
		matches := pattern.FindStringSubmatch(output)
		if len(matches) >= 2 {
			return matches[1]
		}
	}

	return ""
}

func ensureCookieExists(path string) error {
	if path == "" {
		return fmt.Errorf("biliup cookie path is empty")
	}
	if _, err := os.Stat(path); err != nil {
		if os.IsNotExist(err) {
			return fmt.Errorf("biliup cookie not found at %s; run `biliup login --user-cookie %s` to log in", path, path)
		}
		return fmt.Errorf("checking biliup cookie: %w", err)
	}
	return nil
}

type biliupMetadata struct {
	Title       string
	Description string
	Dynamic     string
	Tag         string
}

func (u *BiliupUploader) buildMetadata(path string) biliupMetadata {
	base := filepath.Base(path)
	name := strings.TrimSuffix(base, filepath.Ext(base))
	if strings.TrimSpace(name) == "" {
		name = base
	}
	title := strings.TrimSpace(u.opts.TitlePrefix + name)
	if title == "" {
		title = name
	}

	desc := strings.TrimSpace(u.opts.Description)
	if desc == "" {
		desc = fmt.Sprintf("Uploaded automatically: %s", title)
	}
	dynamic := strings.TrimSpace(u.opts.Dynamic)
	if dynamic == "" {
		dynamic = desc
	}

	tag := strings.Join(filterEmpty(u.opts.Tags), ",")

	return biliupMetadata{
		Title:       title,
		Description: desc,
		Dynamic:     dynamic,
		Tag:         tag,
	}
}

func filterEmpty(values []string) []string {
	result := make([]string, 0, len(values))
	for _, v := range values {
		v = strings.TrimSpace(v)
		if v != "" {
			result = append(result, v)
		}
	}
	return result
}

type prefixedLogger struct {
	prefix string
}

func newPrefixedLogger(prefix string) io.Writer {
	return &prefixedLogger{prefix: prefix}
}

func (p *prefixedLogger) Write(data []byte) (int, error) {
	text := string(data)
	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimSpace(line)
		if line != "" {
			log.Printf("[%s] %s", p.prefix, line)
		}
	}
	return len(data), nil
}
