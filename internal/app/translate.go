package app

import (
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strings"
)

var translateTagRe = regexp.MustCompile(`class="(?:t0|result-container)">(.*?)<`)

// googleTranslate translates text from source to target language using Google Translate.
// Uses the free translate.google.com/m endpoint.
func googleTranslate(text, source, target string) (string, error) {
	if strings.TrimSpace(text) == "" {
		return "", nil
	}

	params := url.Values{
		"sl": {source},
		"tl": {target},
		"q":  {text},
		"hl": {target},
	}
	reqURL := "https://translate.google.com/m?" + params.Encode()

	req, err := http.NewRequest("GET", reqURL, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("User-Agent", "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", fmt.Errorf("google translate request failed: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", fmt.Errorf("reading translate response: %w", err)
	}

	if resp.StatusCode != 200 {
		return "", fmt.Errorf("google translate HTTP %d", resp.StatusCode)
	}

	matches := translateTagRe.FindSubmatch(body)
	if matches == nil {
		return "", fmt.Errorf("could not parse translation from response")
	}

	result := string(matches[1])
	// Unescape basic HTML entities
	result = strings.ReplaceAll(result, "&amp;", "&")
	result = strings.ReplaceAll(result, "&lt;", "<")
	result = strings.ReplaceAll(result, "&gt;", ">")
	result = strings.ReplaceAll(result, "&quot;", "\"")
	result = strings.ReplaceAll(result, "&#39;", "'")
	return result, nil
}

// translateSRT translates the text content of an SRT string from English to Chinese.
// Preserves all SRT formatting (indices, timestamps).
func translateSRT(srtContent string) (string, error) {
	blocks := parseSRTBlocks(srtContent)
	if len(blocks) == 0 {
		return srtContent, nil
	}

	// Translate each line individually for reliability.
	for i := range blocks {
		translated, err := googleTranslate(blocks[i].text, "en", "zh-CN")
		if err != nil {
			// Log but keep original text on failure.
			translated = blocks[i].text
		}
		if translated != "" {
			blocks[i].text = translated
		}
	}

	return rebuildSRT(blocks), nil
}

type srtBlock struct {
	timestamp string
	text      string
}

func parseSRTBlocks(srt string) []srtBlock {
	rawBlocks := regexp.MustCompile(`\n\s*\n`).Split(strings.TrimSpace(srt), -1)
	var blocks []srtBlock

	tsRe := regexp.MustCompile(`\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,.]\d{3}`)
	for _, raw := range rawBlocks {
		lines := strings.Split(strings.TrimSpace(raw), "\n")
		if len(lines) < 3 {
			continue
		}
		if !tsRe.MatchString(lines[1]) {
			continue
		}
		text := strings.TrimSpace(strings.Join(lines[2:], " "))
		if text == "" {
			continue
		}
		blocks = append(blocks, srtBlock{
			timestamp: strings.TrimSpace(lines[1]),
			text:      text,
		})
	}
	return blocks
}

func rebuildSRT(blocks []srtBlock) string {
	var sb strings.Builder
	for i, b := range blocks {
		fmt.Fprintf(&sb, "%d\n%s\n%s\n\n", i+1, b.timestamp, b.text)
	}
	return sb.String()
}
