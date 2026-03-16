package app

import (
	"encoding/json"
	"fmt"
	"io"
	"log"
	"math"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
)

const bilibiliAPI = "https://api.bilibili.com"

// bilibiliCreds holds SESSDATA and bili_jct parsed from biliup's cookies.json.
type bilibiliCreds struct {
	SESSDATA string
	BiliJct  string
}

// loadBilibiliCookies parses biliup's cookies.json for SESSDATA and bili_jct.
func loadBilibiliCookies(path string) (*bilibiliCreds, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("reading cookies: %w", err)
	}

	var raw struct {
		CookieInfo struct {
			Cookies []struct {
				Name  string `json:"name"`
				Value string `json:"value"`
			} `json:"cookies"`
		} `json:"cookie_info"`
	}
	if err := json.Unmarshal(data, &raw); err != nil {
		return nil, fmt.Errorf("parsing cookies JSON: %w", err)
	}

	creds := &bilibiliCreds{}
	for _, c := range raw.CookieInfo.Cookies {
		switch c.Name {
		case "SESSDATA":
			creds.SESSDATA = c.Value
		case "bili_jct":
			creds.BiliJct = c.Value
		}
	}
	if creds.SESSDATA == "" || creds.BiliJct == "" {
		return nil, fmt.Errorf("missing SESSDATA or bili_jct in cookies.json")
	}
	return creds, nil
}

// getCID retrieves the CID (first part) for a Bilibili video.
func getCID(bvid string, sessdata string) (int64, error) {
	req, err := http.NewRequest("GET", bilibiliAPI+"/x/web-interface/view?bvid="+url.QueryEscape(bvid), nil)
	if err != nil {
		return 0, err
	}
	req.Header.Set("Referer", "https://www.bilibili.com")
	req.AddCookie(&http.Cookie{Name: "SESSDATA", Value: sessdata})

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return 0, fmt.Errorf("fetching CID: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return 0, fmt.Errorf("reading CID response: %w", err)
	}

	var result struct {
		Code    int    `json:"code"`
		Message string `json:"message"`
		Data    struct {
			Pages []struct {
				CID int64 `json:"cid"`
			} `json:"pages"`
		} `json:"data"`
	}
	if err := json.Unmarshal(body, &result); err != nil {
		return 0, fmt.Errorf("parsing CID response: %w", err)
	}
	if result.Code != 0 {
		return 0, fmt.Errorf("Bilibili API error: %s", result.Message)
	}
	if len(result.Data.Pages) == 0 {
		return 0, fmt.Errorf("no pages found for %s", bvid)
	}
	return result.Data.Pages[0].CID, nil
}

// bccEntry is a single subtitle entry in Bilibili's BCC format.
type bccEntry struct {
	From     float64 `json:"from"`
	To       float64 `json:"to"`
	Location int     `json:"location"`
	Content  string  `json:"content"`
}

// bccSubtitle is the full BCC subtitle structure.
type bccSubtitle struct {
	FontSize        float64    `json:"font_size"`
	FontColor       string     `json:"font_color"`
	BackgroundAlpha float64    `json:"background_alpha"`
	BackgroundColor string     `json:"background_color"`
	Stroke          string     `json:"Stroke"`
	Body            []bccEntry `json:"body"`
}

var srtTimestampRe = regexp.MustCompile(`(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})`)
var htmlTagRe = regexp.MustCompile(`<[^>]+>`)

// parseSRTTimestamp converts "HH:MM:SS,mmm" to seconds.
func parseSRTTimestamp(ts string) float64 {
	ts = strings.TrimSpace(strings.Replace(ts, ",", ".", 1))
	parts := strings.Split(ts, ":")
	if len(parts) != 3 {
		return 0
	}
	h, _ := strconv.Atoi(parts[0])
	m, _ := strconv.Atoi(parts[1])
	s, _ := strconv.ParseFloat(parts[2], 64)
	return float64(h)*3600 + float64(m)*60 + s
}

// srtToBCC converts SRT subtitle text to Bilibili BCC format.
func srtToBCC(srtText string) *bccSubtitle {
	blocks := regexp.MustCompile(`\n\s*\n`).Split(strings.TrimSpace(srtText), -1)
	var body []bccEntry

	for _, block := range blocks {
		lines := strings.Split(strings.TrimSpace(block), "\n")
		if len(lines) < 3 {
			continue
		}

		matches := srtTimestampRe.FindStringSubmatch(lines[1])
		if matches == nil {
			continue
		}

		start := parseSRTTimestamp(matches[1])
		end := parseSRTTimestamp(matches[2])
		content := strings.TrimSpace(strings.Join(lines[2:], " "))
		content = htmlTagRe.ReplaceAllString(content, "")
		if content == "" {
			continue
		}

		body = append(body, bccEntry{
			From:     math.Round(start*1000) / 1000,
			To:       math.Round(end*1000) / 1000,
			Location: 2,
			Content:  content,
		})
	}

	return &bccSubtitle{
		FontSize:        0.4,
		FontColor:       "#FFFFFF",
		BackgroundAlpha: 0.5,
		BackgroundColor: "#9C27B0",
		Stroke:          "none",
		Body:            body,
	}
}

// uploadSubtitleToBilibili uploads SRT content as CC subtitles to a Bilibili video.
func uploadSubtitleToBilibili(bvid, srtContent, cookiePath string) error {
	creds, err := loadBilibiliCookies(cookiePath)
	if err != nil {
		return fmt.Errorf("loading cookies: %w", err)
	}

	bcc := srtToBCC(srtContent)
	if len(bcc.Body) == 0 {
		return fmt.Errorf("SRT has no usable subtitle entries")
	}

	cid, err := getCID(bvid, creds.SESSDATA)
	if err != nil {
		return fmt.Errorf("getting CID: %w", err)
	}

	bccJSON, err := json.Marshal(bcc)
	if err != nil {
		return fmt.Errorf("marshaling BCC: %w", err)
	}

	form := url.Values{
		"type":   {"1"},
		"oid":    {strconv.FormatInt(cid, 10)},
		"lan":    {"zh-Hans"},
		"bvid":   {bvid},
		"submit": {"true"},
		"sign":   {"false"},
		"csrf":   {creds.BiliJct},
		"data":   {string(bccJSON)},
	}

	req, err := http.NewRequest("POST", bilibiliAPI+"/x/v2/dm/subtitle/draft/save", strings.NewReader(form.Encode()))
	if err != nil {
		return fmt.Errorf("creating request: %w", err)
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("Referer", "https://www.bilibili.com")
	req.AddCookie(&http.Cookie{Name: "SESSDATA", Value: creds.SESSDATA})

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return fmt.Errorf("uploading subtitle: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("reading upload response: %w", err)
	}

	var result struct {
		Code    int    `json:"code"`
		Message string `json:"message"`
	}
	if err := json.Unmarshal(body, &result); err != nil {
		return fmt.Errorf("parsing upload response: %w", err)
	}
	if result.Code != 0 {
		return fmt.Errorf("Bilibili subtitle API error: %s (code=%d)", result.Message, result.Code)
	}

	log.Printf("subtitle: uploaded CC for %s (cid=%d, entries=%d)", bvid, cid, len(bcc.Body))
	return nil
}
