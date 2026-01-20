package app

import (
	"fmt"
	"io"
	"log"
	"os"
	"os/exec"
	"path/filepath"
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

func (u *BiliupUploader) Upload(path string) error {
	binary := u.opts.Binary
	if binary == "" {
		binary = "biliup"
	}
	if _, err := LookPath(binary); err != nil {
		return fmt.Errorf("biliup binary %q not found in PATH; install it from github.com/biliup/biliup or set --biliup-binary", binary)
	}

	cookie := u.opts.CookiePath
	if cookie == "" {
		cookie = "cookies.json"
	}
	if err := ensureCookieExists(cookie); err != nil {
		return err
	}

	meta := u.buildMetadata(path)

	args := []string{"upload", "--user-cookie", cookie, "--limit", strconv.Itoa(u.opts.Limit)}
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

	cmd := exec.Command(binary, args...)
	cmd.Stdout = newPrefixedLogger("biliup")
	cmd.Stderr = newPrefixedLogger("biliup")
	if err := cmd.Run(); err != nil {
		return fmt.Errorf("biliup upload failed: %w", err)
	}
	return nil
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
