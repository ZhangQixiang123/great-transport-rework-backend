package app

import (
	"context"
	"database/sql"
	"encoding/json"
	"time"

	_ "modernc.org/sqlite"
)

type SQLiteStore struct {
	db *sql.DB
}

func NewSQLiteStore(path string) (*SQLiteStore, error) {
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}
	return &SQLiteStore{db: db}, nil
}

func (s *SQLiteStore) EnsureSchema(ctx context.Context) error {
	statements := []string{
		`CREATE TABLE IF NOT EXISTS uploads (
			video_id TEXT PRIMARY KEY,
			channel_id TEXT NOT NULL,
			bilibili_bvid TEXT,
			uploaded_at TIMESTAMP NOT NULL
		);`,
		`CREATE TABLE IF NOT EXISTS channels (
			channel_id TEXT PRIMARY KEY,
			name TEXT,
			url TEXT NOT NULL,
			subscriber_count INTEGER,
			video_count INTEGER,
			last_scanned_at TIMESTAMP,
			scan_frequency_hours INTEGER DEFAULT 6,
			is_active INTEGER DEFAULT 1,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		);`,
		`CREATE TABLE IF NOT EXISTS video_candidates (
			video_id TEXT PRIMARY KEY,
			channel_id TEXT NOT NULL,
			title TEXT,
			description TEXT,
			duration_seconds INTEGER,
			view_count INTEGER,
			like_count INTEGER,
			comment_count INTEGER,
			published_at TIMESTAMP,
			discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			thumbnail_url TEXT,
			tags TEXT,
			category TEXT,
			language TEXT,
			view_velocity REAL,
			engagement_rate REAL,
			FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
		);`,
		`CREATE INDEX IF NOT EXISTS idx_candidates_channel ON video_candidates(channel_id);`,
		`CREATE INDEX IF NOT EXISTS idx_candidates_published ON video_candidates(published_at);`,
		// Phase 2: Rule engine tables
		`CREATE TABLE IF NOT EXISTS filter_rules (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			rule_name TEXT NOT NULL UNIQUE,
			rule_type TEXT NOT NULL,
			field TEXT NOT NULL,
			value TEXT NOT NULL,
			is_active INTEGER DEFAULT 1,
			priority INTEGER DEFAULT 0,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		);`,
		`CREATE TABLE IF NOT EXISTS rule_decisions (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			video_id TEXT NOT NULL,
			rule_passed INTEGER NOT NULL,
			reject_rule_name TEXT,
			reject_reason TEXT,
			evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			FOREIGN KEY (video_id) REFERENCES video_candidates(video_id)
		);`,
		`CREATE INDEX IF NOT EXISTS idx_rule_decisions_video ON rule_decisions(video_id);`,
		`CREATE INDEX IF NOT EXISTS idx_rule_decisions_passed ON rule_decisions(rule_passed);`,
		// Phase 3A: Performance tracking tables
		`CREATE TABLE IF NOT EXISTS upload_performance (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			upload_id TEXT NOT NULL,
			checkpoint_hours INTEGER NOT NULL,
			recorded_at TIMESTAMP NOT NULL,
			views INTEGER DEFAULT 0,
			likes INTEGER DEFAULT 0,
			coins INTEGER DEFAULT 0,
			favorites INTEGER DEFAULT 0,
			shares INTEGER DEFAULT 0,
			danmaku INTEGER DEFAULT 0,
			comments INTEGER DEFAULT 0,
			view_velocity REAL DEFAULT 0,
			engagement_rate REAL DEFAULT 0,
			FOREIGN KEY (upload_id) REFERENCES uploads(video_id)
		);`,
		`CREATE INDEX IF NOT EXISTS idx_upload_performance_upload ON upload_performance(upload_id);`,
		`CREATE INDEX IF NOT EXISTS idx_upload_performance_checkpoint ON upload_performance(checkpoint_hours);`,
		`CREATE UNIQUE INDEX IF NOT EXISTS idx_upload_performance_unique ON upload_performance(upload_id, checkpoint_hours);`,
		`CREATE TABLE IF NOT EXISTS upload_outcomes (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			upload_id TEXT NOT NULL UNIQUE,
			label TEXT NOT NULL,
			labeled_at TIMESTAMP NOT NULL,
			final_views INTEGER DEFAULT 0,
			final_engagement_rate REAL DEFAULT 0,
			final_coins INTEGER DEFAULT 0,
			FOREIGN KEY (upload_id) REFERENCES uploads(video_id)
		);`,
		`CREATE INDEX IF NOT EXISTS idx_upload_outcomes_label ON upload_outcomes(label);`,
		// Phase 3B: Competitor monitoring tables
		`CREATE TABLE IF NOT EXISTS competitor_channels (
			bilibili_uid TEXT PRIMARY KEY,
			name TEXT,
			description TEXT,
			follower_count INTEGER DEFAULT 0,
			video_count INTEGER DEFAULT 0,
			added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			is_active INTEGER DEFAULT 1
		);`,
		`CREATE TABLE IF NOT EXISTS competitor_videos (
			bvid TEXT PRIMARY KEY,
			bilibili_uid TEXT NOT NULL,
			title TEXT,
			description TEXT,
			duration INTEGER DEFAULT 0,
			views INTEGER DEFAULT 0,
			likes INTEGER DEFAULT 0,
			coins INTEGER DEFAULT 0,
			favorites INTEGER DEFAULT 0,
			shares INTEGER DEFAULT 0,
			danmaku INTEGER DEFAULT 0,
			comments INTEGER DEFAULT 0,
			publish_time TIMESTAMP,
			collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			youtube_source_id TEXT,
			label TEXT,
			FOREIGN KEY (bilibili_uid) REFERENCES competitor_channels(bilibili_uid)
		);`,
		`CREATE INDEX IF NOT EXISTS idx_competitor_videos_uid ON competitor_videos(bilibili_uid);`,
		`CREATE INDEX IF NOT EXISTS idx_competitor_videos_label ON competitor_videos(label);`,
		`CREATE INDEX IF NOT EXISTS idx_competitor_videos_youtube ON competitor_videos(youtube_source_id);`,
	}
	for _, stmt := range statements {
		if _, err := s.db.ExecContext(ctx, stmt); err != nil {
			return err
		}
	}

	// Migration: Add bilibili_bvid column to uploads table if it doesn't exist
	if err := s.migrateAddBilibiliBvid(ctx); err != nil {
		return err
	}

	return nil
}

// migrateAddBilibiliBvid adds the bilibili_bvid column to existing uploads table.
func (s *SQLiteStore) migrateAddBilibiliBvid(ctx context.Context) error {
	// Check if column exists by querying table info
	rows, err := s.db.QueryContext(ctx, `PRAGMA table_info(uploads)`)
	if err != nil {
		return err
	}
	defer rows.Close()

	hasBvid := false
	for rows.Next() {
		var cid int
		var name, ctype string
		var notnull, pk int
		var dflt sql.NullString
		if err := rows.Scan(&cid, &name, &ctype, &notnull, &dflt, &pk); err != nil {
			return err
		}
		if name == "bilibili_bvid" {
			hasBvid = true
			break
		}
	}

	if !hasBvid {
		_, err := s.db.ExecContext(ctx, `ALTER TABLE uploads ADD COLUMN bilibili_bvid TEXT`)
		if err != nil {
			return err
		}
	}
	return nil
}

func (s *SQLiteStore) IsUploaded(ctx context.Context, videoID string) (bool, error) {
	var count int
	if err := s.db.QueryRowContext(ctx, `SELECT COUNT(1) FROM uploads WHERE video_id = ?`, videoID).Scan(&count); err != nil {
		return false, err
	}
	return count > 0, nil
}

func (s *SQLiteStore) MarkUploaded(ctx context.Context, videoID, channelID string) error {
	return s.MarkUploadedWithBvid(ctx, videoID, channelID, "")
}

// MarkUploadedWithBvid records an upload with optional Bilibili video ID.
func (s *SQLiteStore) MarkUploadedWithBvid(ctx context.Context, videoID, channelID, bilibiliBvid string) error {
	if channelID == "" {
		channelID = "unknown"
	}
	_, err := s.db.ExecContext(ctx, `
INSERT INTO uploads (video_id, channel_id, bilibili_bvid, uploaded_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(video_id) DO UPDATE SET
	channel_id = excluded.channel_id,
	bilibili_bvid = COALESCE(excluded.bilibili_bvid, uploads.bilibili_bvid),
	uploaded_at = excluded.uploaded_at;`, videoID, channelID, nullableString(bilibiliBvid), time.Now().UTC())
	return err
}

// UpdateBilibiliBvid updates the Bilibili video ID for an existing upload.
func (s *SQLiteStore) UpdateBilibiliBvid(ctx context.Context, videoID, bilibiliBvid string) error {
	_, err := s.db.ExecContext(ctx, `UPDATE uploads SET bilibili_bvid = ? WHERE video_id = ?`, bilibiliBvid, videoID)
	return err
}

// GetUpload retrieves an upload record by video ID.
func (s *SQLiteStore) GetUpload(ctx context.Context, videoID string) (*Upload, error) {
	row := s.db.QueryRowContext(ctx, `SELECT video_id, channel_id, bilibili_bvid, uploaded_at FROM uploads WHERE video_id = ?`, videoID)

	var u Upload
	var bvid sql.NullString
	err := row.Scan(&u.VideoID, &u.ChannelID, &bvid, &u.UploadedAt)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	u.BilibiliBvid = bvid.String
	return &u, nil
}

func nullableString(s string) interface{} {
	if s == "" {
		return nil
	}
	return s
}

// AddChannel inserts a new channel to monitor.
func (s *SQLiteStore) AddChannel(ctx context.Context, ch Channel) error {
	_, err := s.db.ExecContext(ctx, `
INSERT INTO channels (channel_id, name, url, subscriber_count, video_count, scan_frequency_hours, is_active, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(channel_id) DO UPDATE SET
	name = COALESCE(excluded.name, channels.name),
	url = excluded.url,
	subscriber_count = COALESCE(excluded.subscriber_count, channels.subscriber_count),
	video_count = COALESCE(excluded.video_count, channels.video_count),
	is_active = 1;`,
		ch.ChannelID, ch.Name, ch.URL, ch.SubscriberCount, ch.VideoCount,
		ch.ScanFrequencyHours, boolToInt(ch.IsActive), time.Now().UTC())
	return err
}

// GetChannel retrieves a channel by ID.
func (s *SQLiteStore) GetChannel(ctx context.Context, channelID string) (*Channel, error) {
	row := s.db.QueryRowContext(ctx, `
SELECT channel_id, name, url, subscriber_count, video_count, last_scanned_at, scan_frequency_hours, is_active, created_at
FROM channels WHERE channel_id = ?`, channelID)

	var ch Channel
	var name, lastScanned sql.NullString
	var subCount, vidCount, scanFreq sql.NullInt64
	var isActive int
	err := row.Scan(&ch.ChannelID, &name, &ch.URL, &subCount, &vidCount, &lastScanned, &scanFreq, &isActive, &ch.CreatedAt)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	ch.Name = name.String
	ch.SubscriberCount = int(subCount.Int64)
	ch.VideoCount = int(vidCount.Int64)
	ch.ScanFrequencyHours = int(scanFreq.Int64)
	if ch.ScanFrequencyHours == 0 {
		ch.ScanFrequencyHours = 6
	}
	ch.IsActive = isActive == 1
	if lastScanned.Valid {
		t, _ := time.Parse(time.RFC3339, lastScanned.String)
		ch.LastScannedAt = &t
	}
	return &ch, nil
}

// ListActiveChannels returns all channels that are active.
func (s *SQLiteStore) ListActiveChannels(ctx context.Context) ([]Channel, error) {
	rows, err := s.db.QueryContext(ctx, `
SELECT channel_id, name, url, subscriber_count, video_count, last_scanned_at, scan_frequency_hours, is_active, created_at
FROM channels WHERE is_active = 1 ORDER BY created_at`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var channels []Channel
	for rows.Next() {
		var ch Channel
		var name, lastScanned sql.NullString
		var subCount, vidCount, scanFreq sql.NullInt64
		var isActive int
		if err := rows.Scan(&ch.ChannelID, &name, &ch.URL, &subCount, &vidCount, &lastScanned, &scanFreq, &isActive, &ch.CreatedAt); err != nil {
			return nil, err
		}
		ch.Name = name.String
		ch.SubscriberCount = int(subCount.Int64)
		ch.VideoCount = int(vidCount.Int64)
		ch.ScanFrequencyHours = int(scanFreq.Int64)
		if ch.ScanFrequencyHours == 0 {
			ch.ScanFrequencyHours = 6
		}
		ch.IsActive = isActive == 1
		if lastScanned.Valid {
			t, _ := time.Parse(time.RFC3339, lastScanned.String)
			ch.LastScannedAt = &t
		}
		channels = append(channels, ch)
	}
	return channels, rows.Err()
}

// UpdateChannelScanned updates the last_scanned_at timestamp for a channel.
func (s *SQLiteStore) UpdateChannelScanned(ctx context.Context, channelID string) error {
	_, err := s.db.ExecContext(ctx, `UPDATE channels SET last_scanned_at = ? WHERE channel_id = ?`, time.Now().UTC(), channelID)
	return err
}

// DeactivateChannel marks a channel as inactive.
func (s *SQLiteStore) DeactivateChannel(ctx context.Context, channelID string) error {
	_, err := s.db.ExecContext(ctx, `UPDATE channels SET is_active = 0 WHERE channel_id = ?`, channelID)
	return err
}

// UpsertCandidate inserts or updates a video candidate.
func (s *SQLiteStore) UpsertCandidate(ctx context.Context, vc VideoCandidate) error {
	tagsJSON, _ := json.Marshal(vc.Tags)
	_, err := s.db.ExecContext(ctx, `
INSERT INTO video_candidates (video_id, channel_id, title, description, duration_seconds, view_count, like_count, comment_count, published_at, thumbnail_url, tags, category, language, view_velocity, engagement_rate)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(video_id) DO UPDATE SET
	title = excluded.title,
	description = excluded.description,
	duration_seconds = excluded.duration_seconds,
	view_count = excluded.view_count,
	like_count = excluded.like_count,
	comment_count = excluded.comment_count,
	thumbnail_url = excluded.thumbnail_url,
	tags = excluded.tags,
	category = excluded.category,
	language = excluded.language,
	view_velocity = excluded.view_velocity,
	engagement_rate = excluded.engagement_rate;`,
		vc.VideoID, vc.ChannelID, vc.Title, vc.Description, vc.DurationSeconds,
		vc.ViewCount, vc.LikeCount, vc.CommentCount, vc.PublishedAt,
		vc.ThumbnailURL, string(tagsJSON), vc.Category, vc.Language,
		vc.ViewVelocity, vc.EngagementRate)
	return err
}

// GetCandidate retrieves a video candidate by ID.
func (s *SQLiteStore) GetCandidate(ctx context.Context, videoID string) (*VideoCandidate, error) {
	row := s.db.QueryRowContext(ctx, `
SELECT video_id, channel_id, title, description, duration_seconds, view_count, like_count, comment_count, published_at, discovered_at, thumbnail_url, tags, category, language, view_velocity, engagement_rate
FROM video_candidates WHERE video_id = ?`, videoID)

	var vc VideoCandidate
	var title, desc, thumbURL, tagsJSON, category, language sql.NullString
	var publishedAt sql.NullString
	var duration, views, likes, comments sql.NullInt64
	var velocity, engagement sql.NullFloat64

	err := row.Scan(&vc.VideoID, &vc.ChannelID, &title, &desc, &duration, &views, &likes, &comments,
		&publishedAt, &vc.DiscoveredAt, &thumbURL, &tagsJSON, &category, &language, &velocity, &engagement)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	vc.Title = title.String
	vc.Description = desc.String
	vc.DurationSeconds = int(duration.Int64)
	vc.ViewCount = int(views.Int64)
	vc.LikeCount = int(likes.Int64)
	vc.CommentCount = int(comments.Int64)
	vc.ThumbnailURL = thumbURL.String
	vc.Category = category.String
	vc.Language = language.String
	vc.ViewVelocity = velocity.Float64
	vc.EngagementRate = engagement.Float64
	if publishedAt.Valid {
		t, _ := time.Parse(time.RFC3339, publishedAt.String)
		vc.PublishedAt = &t
	}
	if tagsJSON.String != "" {
		_ = json.Unmarshal([]byte(tagsJSON.String), &vc.Tags)
	}
	return &vc, nil
}

// ListCandidatesByChannel returns video candidates for a specific channel.
func (s *SQLiteStore) ListCandidatesByChannel(ctx context.Context, channelID string, limit int) ([]VideoCandidate, error) {
	rows, err := s.db.QueryContext(ctx, `
SELECT video_id, channel_id, title, description, duration_seconds, view_count, like_count, comment_count, published_at, discovered_at, thumbnail_url, tags, category, language, view_velocity, engagement_rate
FROM video_candidates WHERE channel_id = ? ORDER BY published_at DESC LIMIT ?`, channelID, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanCandidates(rows)
}

// ListPendingCandidates returns video candidates that haven't been uploaded yet.
func (s *SQLiteStore) ListPendingCandidates(ctx context.Context, limit int) ([]VideoCandidate, error) {
	rows, err := s.db.QueryContext(ctx, `
SELECT vc.video_id, vc.channel_id, vc.title, vc.description, vc.duration_seconds, vc.view_count, vc.like_count, vc.comment_count, vc.published_at, vc.discovered_at, vc.thumbnail_url, vc.tags, vc.category, vc.language, vc.view_velocity, vc.engagement_rate
FROM video_candidates vc
LEFT JOIN uploads u ON vc.video_id = u.video_id
WHERE u.video_id IS NULL
ORDER BY vc.view_velocity DESC, vc.engagement_rate DESC
LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanCandidates(rows)
}

// UpdateCandidateMetrics updates view, like, and comment counts for a candidate.
func (s *SQLiteStore) UpdateCandidateMetrics(ctx context.Context, videoID string, views, likes, comments int) error {
	_, err := s.db.ExecContext(ctx, `
UPDATE video_candidates SET view_count = ?, like_count = ?, comment_count = ? WHERE video_id = ?`,
		views, likes, comments, videoID)
	return err
}

func scanCandidates(rows *sql.Rows) ([]VideoCandidate, error) {
	var candidates []VideoCandidate
	for rows.Next() {
		var vc VideoCandidate
		var title, desc, thumbURL, tagsJSON, category, language sql.NullString
		var publishedAt sql.NullString
		var duration, views, likes, comments sql.NullInt64
		var velocity, engagement sql.NullFloat64

		if err := rows.Scan(&vc.VideoID, &vc.ChannelID, &title, &desc, &duration, &views, &likes, &comments,
			&publishedAt, &vc.DiscoveredAt, &thumbURL, &tagsJSON, &category, &language, &velocity, &engagement); err != nil {
			return nil, err
		}
		vc.Title = title.String
		vc.Description = desc.String
		vc.DurationSeconds = int(duration.Int64)
		vc.ViewCount = int(views.Int64)
		vc.LikeCount = int(likes.Int64)
		vc.CommentCount = int(comments.Int64)
		vc.ThumbnailURL = thumbURL.String
		vc.Category = category.String
		vc.Language = language.String
		vc.ViewVelocity = velocity.Float64
		vc.EngagementRate = engagement.Float64
		if publishedAt.Valid {
			t, _ := time.Parse(time.RFC3339, publishedAt.String)
			vc.PublishedAt = &t
		}
		if tagsJSON.String != "" {
			_ = json.Unmarshal([]byte(tagsJSON.String), &vc.Tags)
		}
		candidates = append(candidates, vc)
	}
	return candidates, rows.Err()
}

func boolToInt(b bool) int {
	if b {
		return 1
	}
	return 0
}

// AddRule inserts a new filter rule.
func (s *SQLiteStore) AddRule(ctx context.Context, rule FilterRule) error {
	_, err := s.db.ExecContext(ctx, `
INSERT INTO filter_rules (rule_name, rule_type, field, value, is_active, priority)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(rule_name) DO UPDATE SET
	rule_type = excluded.rule_type,
	field = excluded.field,
	value = excluded.value,
	is_active = excluded.is_active,
	priority = excluded.priority;`,
		rule.RuleName, rule.RuleType, rule.Field, rule.Value,
		boolToInt(rule.IsActive), rule.Priority)
	return err
}

// GetRule retrieves a filter rule by name.
func (s *SQLiteStore) GetRule(ctx context.Context, ruleName string) (*FilterRule, error) {
	row := s.db.QueryRowContext(ctx, `
SELECT id, rule_name, rule_type, field, value, is_active, priority, created_at
FROM filter_rules WHERE rule_name = ?`, ruleName)

	var rule FilterRule
	var isActive int
	err := row.Scan(&rule.ID, &rule.RuleName, &rule.RuleType, &rule.Field, &rule.Value,
		&isActive, &rule.Priority, &rule.CreatedAt)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	rule.IsActive = isActive == 1
	return &rule, nil
}

// ListActiveRules returns all active filter rules.
func (s *SQLiteStore) ListActiveRules(ctx context.Context) ([]FilterRule, error) {
	rows, err := s.db.QueryContext(ctx, `
SELECT id, rule_name, rule_type, field, value, is_active, priority, created_at
FROM filter_rules WHERE is_active = 1 ORDER BY priority DESC, rule_name`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	return scanRules(rows)
}

// ListAllRules returns all filter rules (active and inactive).
func (s *SQLiteStore) ListAllRules(ctx context.Context) ([]FilterRule, error) {
	rows, err := s.db.QueryContext(ctx, `
SELECT id, rule_name, rule_type, field, value, is_active, priority, created_at
FROM filter_rules ORDER BY priority DESC, rule_name`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	return scanRules(rows)
}

// UpdateRule updates a filter rule's value.
func (s *SQLiteStore) UpdateRule(ctx context.Context, ruleName string, value string) error {
	_, err := s.db.ExecContext(ctx, `UPDATE filter_rules SET value = ? WHERE rule_name = ?`, value, ruleName)
	return err
}

// DeleteRule removes a filter rule.
func (s *SQLiteStore) DeleteRule(ctx context.Context, ruleName string) error {
	_, err := s.db.ExecContext(ctx, `DELETE FROM filter_rules WHERE rule_name = ?`, ruleName)
	return err
}

// RecordRuleDecision records the result of rule evaluation.
func (s *SQLiteStore) RecordRuleDecision(ctx context.Context, decision RuleDecision) error {
	_, err := s.db.ExecContext(ctx, `
INSERT INTO rule_decisions (video_id, rule_passed, reject_rule_name, reject_reason, evaluated_at)
VALUES (?, ?, ?, ?, ?)`,
		decision.VideoID, boolToInt(decision.RulePassed), decision.RejectRuleName,
		decision.RejectReason, decision.EvaluatedAt)
	return err
}

// GetRuleDecision retrieves the latest rule decision for a video.
func (s *SQLiteStore) GetRuleDecision(ctx context.Context, videoID string) (*RuleDecision, error) {
	row := s.db.QueryRowContext(ctx, `
SELECT id, video_id, rule_passed, reject_rule_name, reject_reason, evaluated_at
FROM rule_decisions WHERE video_id = ? ORDER BY evaluated_at DESC LIMIT 1`, videoID)

	var d RuleDecision
	var rulePassed int
	var rejectRuleName, rejectReason sql.NullString
	err := row.Scan(&d.ID, &d.VideoID, &rulePassed, &rejectRuleName, &rejectReason, &d.EvaluatedAt)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	d.RulePassed = rulePassed == 1
	d.RejectRuleName = rejectRuleName.String
	d.RejectReason = rejectReason.String
	return &d, nil
}

// ListFilteredCandidates returns candidates that passed rule filtering.
func (s *SQLiteStore) ListFilteredCandidates(ctx context.Context, limit int) ([]VideoCandidate, error) {
	rows, err := s.db.QueryContext(ctx, `
SELECT vc.video_id, vc.channel_id, vc.title, vc.description, vc.duration_seconds,
       vc.view_count, vc.like_count, vc.comment_count, vc.published_at, vc.discovered_at,
       vc.thumbnail_url, vc.tags, vc.category, vc.language, vc.view_velocity, vc.engagement_rate
FROM video_candidates vc
INNER JOIN rule_decisions rd ON vc.video_id = rd.video_id
LEFT JOIN uploads u ON vc.video_id = u.video_id
WHERE rd.rule_passed = 1 AND u.video_id IS NULL
  AND rd.id = (SELECT MAX(id) FROM rule_decisions WHERE video_id = vc.video_id)
ORDER BY vc.view_velocity DESC, vc.engagement_rate DESC
LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanCandidates(rows)
}

// ListRejectedCandidates returns candidates that were rejected by rules.
func (s *SQLiteStore) ListRejectedCandidates(ctx context.Context, limit int) ([]RejectedCandidate, error) {
	rows, err := s.db.QueryContext(ctx, `
SELECT vc.video_id, vc.title, vc.view_count, vc.published_at,
       rd.reject_rule_name, rd.reject_reason, rd.evaluated_at
FROM video_candidates vc
INNER JOIN rule_decisions rd ON vc.video_id = rd.video_id
WHERE rd.rule_passed = 0
  AND rd.id = (SELECT MAX(id) FROM rule_decisions WHERE video_id = vc.video_id)
ORDER BY rd.evaluated_at DESC
LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var rejected []RejectedCandidate
	for rows.Next() {
		var r RejectedCandidate
		var title, rejectRuleName, rejectReason sql.NullString
		var views sql.NullInt64
		var publishedAt sql.NullString

		if err := rows.Scan(&r.VideoID, &title, &views, &publishedAt,
			&rejectRuleName, &rejectReason, &r.EvaluatedAt); err != nil {
			return nil, err
		}
		r.Title = title.String
		r.ViewCount = int(views.Int64)
		r.RejectRuleName = rejectRuleName.String
		r.RejectReason = rejectReason.String
		if publishedAt.Valid {
			t, _ := time.Parse(time.RFC3339, publishedAt.String)
			r.PublishedAt = &t
		}
		rejected = append(rejected, r)
	}
	return rejected, rows.Err()
}

// ListUnevaluatedCandidates returns candidates that haven't been evaluated by rules yet.
func (s *SQLiteStore) ListUnevaluatedCandidates(ctx context.Context, limit int) ([]VideoCandidate, error) {
	rows, err := s.db.QueryContext(ctx, `
SELECT vc.video_id, vc.channel_id, vc.title, vc.description, vc.duration_seconds,
       vc.view_count, vc.like_count, vc.comment_count, vc.published_at, vc.discovered_at,
       vc.thumbnail_url, vc.tags, vc.category, vc.language, vc.view_velocity, vc.engagement_rate
FROM video_candidates vc
LEFT JOIN uploads u ON vc.video_id = u.video_id
WHERE u.video_id IS NULL
  AND NOT EXISTS (SELECT 1 FROM rule_decisions rd WHERE rd.video_id = vc.video_id)
ORDER BY vc.view_velocity DESC, vc.engagement_rate DESC
LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanCandidates(rows)
}

func scanRules(rows *sql.Rows) ([]FilterRule, error) {
	var rules []FilterRule
	for rows.Next() {
		var rule FilterRule
		var isActive int
		if err := rows.Scan(&rule.ID, &rule.RuleName, &rule.RuleType, &rule.Field,
			&rule.Value, &isActive, &rule.Priority, &rule.CreatedAt); err != nil {
			return nil, err
		}
		rule.IsActive = isActive == 1
		rules = append(rules, rule)
	}
	return rules, rows.Err()
}

// Phase 3A: Performance Tracking Methods

// SaveUploadPerformance records performance metrics for an upload at a checkpoint.
func (s *SQLiteStore) SaveUploadPerformance(ctx context.Context, perf UploadPerformance) error {
	_, err := s.db.ExecContext(ctx, `
INSERT INTO upload_performance (upload_id, checkpoint_hours, recorded_at, views, likes, coins, favorites, shares, danmaku, comments, view_velocity, engagement_rate)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(upload_id, checkpoint_hours) DO UPDATE SET
	recorded_at = excluded.recorded_at,
	views = excluded.views,
	likes = excluded.likes,
	coins = excluded.coins,
	favorites = excluded.favorites,
	shares = excluded.shares,
	danmaku = excluded.danmaku,
	comments = excluded.comments,
	view_velocity = excluded.view_velocity,
	engagement_rate = excluded.engagement_rate;`,
		perf.UploadID, perf.CheckpointHours, perf.RecordedAt,
		perf.Views, perf.Likes, perf.Coins, perf.Favorites,
		perf.Shares, perf.Danmaku, perf.Comments,
		perf.ViewVelocity, perf.EngagementRate)
	return err
}

// GetUploadPerformance retrieves all performance records for an upload.
func (s *SQLiteStore) GetUploadPerformance(ctx context.Context, uploadID string) ([]UploadPerformance, error) {
	rows, err := s.db.QueryContext(ctx, `
SELECT id, upload_id, checkpoint_hours, recorded_at, views, likes, coins, favorites, shares, danmaku, comments, view_velocity, engagement_rate
FROM upload_performance WHERE upload_id = ? ORDER BY checkpoint_hours`, uploadID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var perfs []UploadPerformance
	for rows.Next() {
		var p UploadPerformance
		if err := rows.Scan(&p.ID, &p.UploadID, &p.CheckpointHours, &p.RecordedAt,
			&p.Views, &p.Likes, &p.Coins, &p.Favorites, &p.Shares, &p.Danmaku, &p.Comments,
			&p.ViewVelocity, &p.EngagementRate); err != nil {
			return nil, err
		}
		perfs = append(perfs, p)
	}
	return perfs, rows.Err()
}

// GetUploadsForTracking returns uploads that need performance tracking.
// It returns uploads with Bilibili bvid that are due for a checkpoint.
func (s *SQLiteStore) GetUploadsForTracking(ctx context.Context, checkpointHours int) ([]Upload, error) {
	rows, err := s.db.QueryContext(ctx, `
SELECT u.video_id, u.channel_id, u.bilibili_bvid, u.uploaded_at
FROM uploads u
WHERE u.bilibili_bvid IS NOT NULL AND u.bilibili_bvid != ''
  AND datetime(u.uploaded_at, '+' || ? || ' hours') <= datetime('now')
  AND NOT EXISTS (
    SELECT 1 FROM upload_performance up
    WHERE up.upload_id = u.video_id AND up.checkpoint_hours = ?
  )
ORDER BY u.uploaded_at`, checkpointHours, checkpointHours)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var uploads []Upload
	for rows.Next() {
		var u Upload
		var bvid sql.NullString
		if err := rows.Scan(&u.VideoID, &u.ChannelID, &bvid, &u.UploadedAt); err != nil {
			return nil, err
		}
		u.BilibiliBvid = bvid.String
		uploads = append(uploads, u)
	}
	return uploads, rows.Err()
}

// GetAllUploadsWithBvid returns all uploads that have a Bilibili bvid.
func (s *SQLiteStore) GetAllUploadsWithBvid(ctx context.Context) ([]Upload, error) {
	rows, err := s.db.QueryContext(ctx, `
SELECT video_id, channel_id, bilibili_bvid, uploaded_at
FROM uploads
WHERE bilibili_bvid IS NOT NULL AND bilibili_bvid != ''
ORDER BY uploaded_at DESC`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var uploads []Upload
	for rows.Next() {
		var u Upload
		var bvid sql.NullString
		if err := rows.Scan(&u.VideoID, &u.ChannelID, &bvid, &u.UploadedAt); err != nil {
			return nil, err
		}
		u.BilibiliBvid = bvid.String
		uploads = append(uploads, u)
	}
	return uploads, rows.Err()
}

// SaveUploadOutcome records the final outcome label for an upload.
func (s *SQLiteStore) SaveUploadOutcome(ctx context.Context, outcome UploadOutcome) error {
	_, err := s.db.ExecContext(ctx, `
INSERT INTO upload_outcomes (upload_id, label, labeled_at, final_views, final_engagement_rate, final_coins)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(upload_id) DO UPDATE SET
	label = excluded.label,
	labeled_at = excluded.labeled_at,
	final_views = excluded.final_views,
	final_engagement_rate = excluded.final_engagement_rate,
	final_coins = excluded.final_coins;`,
		outcome.UploadID, outcome.Label, outcome.LabeledAt,
		outcome.FinalViews, outcome.FinalEngagementRate, outcome.FinalCoins)
	return err
}

// GetUploadOutcome retrieves the outcome for an upload.
func (s *SQLiteStore) GetUploadOutcome(ctx context.Context, uploadID string) (*UploadOutcome, error) {
	row := s.db.QueryRowContext(ctx, `
SELECT id, upload_id, label, labeled_at, final_views, final_engagement_rate, final_coins
FROM upload_outcomes WHERE upload_id = ?`, uploadID)

	var o UploadOutcome
	err := row.Scan(&o.ID, &o.UploadID, &o.Label, &o.LabeledAt,
		&o.FinalViews, &o.FinalEngagementRate, &o.FinalCoins)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &o, nil
}

// GetUploadStats returns aggregate statistics about uploads.
func (s *SQLiteStore) GetUploadStats(ctx context.Context) (*UploadStats, error) {
	stats := &UploadStats{}

	// Total uploads
	if err := s.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM uploads`).Scan(&stats.TotalUploads); err != nil {
		return nil, err
	}

	// Uploads with bvid
	if err := s.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM uploads WHERE bilibili_bvid IS NOT NULL AND bilibili_bvid != ''`).Scan(&stats.UploadsWithBvid); err != nil {
		return nil, err
	}

	// Uploads with performance data
	if err := s.db.QueryRowContext(ctx, `SELECT COUNT(DISTINCT upload_id) FROM upload_performance`).Scan(&stats.UploadsWithPerformance); err != nil {
		return nil, err
	}

	// Uploads by label
	rows, err := s.db.QueryContext(ctx, `SELECT label, COUNT(*) FROM upload_outcomes GROUP BY label`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	stats.UploadsByLabel = make(map[string]int)
	for rows.Next() {
		var label string
		var count int
		if err := rows.Scan(&label, &count); err != nil {
			return nil, err
		}
		stats.UploadsByLabel[label] = count
	}

	// Average metrics from latest checkpoint
	row := s.db.QueryRowContext(ctx, `
SELECT COALESCE(AVG(views), 0), COALESCE(AVG(likes), 0), COALESCE(AVG(coins), 0), COALESCE(AVG(engagement_rate), 0)
FROM upload_performance
WHERE (upload_id, checkpoint_hours) IN (
	SELECT upload_id, MAX(checkpoint_hours) FROM upload_performance GROUP BY upload_id
)`)
	if err := row.Scan(&stats.AvgViews, &stats.AvgLikes, &stats.AvgCoins, &stats.AvgEngagementRate); err != nil {
		return nil, err
	}

	return stats, nil
}

// ListRecentUploadsWithPerformance returns recent uploads with their latest performance.
func (s *SQLiteStore) ListRecentUploadsWithPerformance(ctx context.Context, limit int) ([]UploadWithPerformance, error) {
	rows, err := s.db.QueryContext(ctx, `
SELECT u.video_id, u.channel_id, u.bilibili_bvid, u.uploaded_at,
       COALESCE(up.views, 0), COALESCE(up.likes, 0), COALESCE(up.coins, 0),
       COALESCE(up.engagement_rate, 0), COALESCE(up.checkpoint_hours, 0),
       COALESCE(uo.label, '')
FROM uploads u
LEFT JOIN upload_performance up ON u.video_id = up.upload_id
  AND up.checkpoint_hours = (SELECT MAX(checkpoint_hours) FROM upload_performance WHERE upload_id = u.video_id)
LEFT JOIN upload_outcomes uo ON u.video_id = uo.upload_id
WHERE u.bilibili_bvid IS NOT NULL AND u.bilibili_bvid != ''
ORDER BY u.uploaded_at DESC
LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var results []UploadWithPerformance
	for rows.Next() {
		var uwp UploadWithPerformance
		var bvid sql.NullString
		if err := rows.Scan(&uwp.VideoID, &uwp.ChannelID, &bvid, &uwp.UploadedAt,
			&uwp.Views, &uwp.Likes, &uwp.Coins, &uwp.EngagementRate, &uwp.LatestCheckpoint, &uwp.Label); err != nil {
			return nil, err
		}
		uwp.BilibiliBvid = bvid.String
		results = append(results, uwp)
	}
	return results, rows.Err()
}

// Phase 3B: Competitor Monitoring Methods

// CompetitorChannel represents a Bilibili transporter channel to monitor.
type CompetitorChannel struct {
	BilibiliUID   string
	Name          string
	Description   string
	FollowerCount int
	VideoCount    int
	AddedAt       time.Time
	IsActive      bool
}

// CompetitorVideo represents a video from a competitor channel.
type CompetitorVideo struct {
	Bvid            string
	BilibiliUID     string
	Title           string
	Description     string
	Duration        int
	Views           int
	Likes           int
	Coins           int
	Favorites       int
	Shares          int
	Danmaku         int
	Comments        int
	PublishTime     *time.Time
	CollectedAt     time.Time
	YoutubeSourceID string
	Label           string
}

// CompetitorStats holds aggregate statistics about competitor data.
type CompetitorStats struct {
	TotalChannels   int
	ActiveChannels  int
	TotalVideos     int
	LabeledVideos   int
	UnlabeledVideos int
}

// TrainingDataSummary holds counts of videos by label.
type TrainingDataSummary struct {
	Viral      int
	Successful int
	Standard   int
	Failed     int
	Unlabeled  int
	Total      int
}

// AddCompetitorChannel inserts a new competitor channel to monitor.
func (s *SQLiteStore) AddCompetitorChannel(ctx context.Context, ch CompetitorChannel) error {
	_, err := s.db.ExecContext(ctx, `
INSERT INTO competitor_channels (bilibili_uid, name, description, follower_count, video_count, added_at, is_active)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(bilibili_uid) DO UPDATE SET
	name = COALESCE(excluded.name, competitor_channels.name),
	description = COALESCE(excluded.description, competitor_channels.description),
	follower_count = excluded.follower_count,
	video_count = excluded.video_count,
	is_active = 1;`,
		ch.BilibiliUID, ch.Name, ch.Description, ch.FollowerCount, ch.VideoCount,
		time.Now().UTC(), boolToInt(ch.IsActive))
	return err
}

// GetCompetitorChannel retrieves a competitor channel by UID.
func (s *SQLiteStore) GetCompetitorChannel(ctx context.Context, uid string) (*CompetitorChannel, error) {
	row := s.db.QueryRowContext(ctx, `
SELECT bilibili_uid, name, description, follower_count, video_count, added_at, is_active
FROM competitor_channels WHERE bilibili_uid = ?`, uid)

	var ch CompetitorChannel
	var name, desc sql.NullString
	var isActive int
	err := row.Scan(&ch.BilibiliUID, &name, &desc, &ch.FollowerCount, &ch.VideoCount, &ch.AddedAt, &isActive)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	ch.Name = name.String
	ch.Description = desc.String
	ch.IsActive = isActive == 1
	return &ch, nil
}

// ListCompetitorChannels returns all active competitor channels.
func (s *SQLiteStore) ListCompetitorChannels(ctx context.Context) ([]CompetitorChannel, error) {
	rows, err := s.db.QueryContext(ctx, `
SELECT bilibili_uid, name, description, follower_count, video_count, added_at, is_active
FROM competitor_channels WHERE is_active = 1 ORDER BY added_at`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var channels []CompetitorChannel
	for rows.Next() {
		var ch CompetitorChannel
		var name, desc sql.NullString
		var isActive int
		if err := rows.Scan(&ch.BilibiliUID, &name, &desc, &ch.FollowerCount, &ch.VideoCount, &ch.AddedAt, &isActive); err != nil {
			return nil, err
		}
		ch.Name = name.String
		ch.Description = desc.String
		ch.IsActive = isActive == 1
		channels = append(channels, ch)
	}
	return channels, rows.Err()
}

// DeactivateCompetitorChannel marks a competitor channel as inactive.
func (s *SQLiteStore) DeactivateCompetitorChannel(ctx context.Context, uid string) error {
	_, err := s.db.ExecContext(ctx, `UPDATE competitor_channels SET is_active = 0 WHERE bilibili_uid = ?`, uid)
	return err
}

// GetCompetitorStats returns aggregate statistics about competitor data.
func (s *SQLiteStore) GetCompetitorStats(ctx context.Context) (*CompetitorStats, error) {
	stats := &CompetitorStats{}

	// Total channels
	if err := s.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM competitor_channels`).Scan(&stats.TotalChannels); err != nil {
		return nil, err
	}

	// Active channels
	if err := s.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM competitor_channels WHERE is_active = 1`).Scan(&stats.ActiveChannels); err != nil {
		return nil, err
	}

	// Total videos
	if err := s.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM competitor_videos`).Scan(&stats.TotalVideos); err != nil {
		return nil, err
	}

	// Labeled videos
	if err := s.db.QueryRowContext(ctx, `SELECT COUNT(*) FROM competitor_videos WHERE label IS NOT NULL AND label != ''`).Scan(&stats.LabeledVideos); err != nil {
		return nil, err
	}

	stats.UnlabeledVideos = stats.TotalVideos - stats.LabeledVideos
	return stats, nil
}

// GetTrainingDataSummary returns counts of competitor videos by label.
func (s *SQLiteStore) GetTrainingDataSummary(ctx context.Context) (*TrainingDataSummary, error) {
	summary := &TrainingDataSummary{}

	rows, err := s.db.QueryContext(ctx, `
SELECT COALESCE(label, 'unlabeled') as lbl, COUNT(*) as cnt
FROM competitor_videos
GROUP BY COALESCE(label, 'unlabeled')`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	for rows.Next() {
		var label string
		var count int
		if err := rows.Scan(&label, &count); err != nil {
			return nil, err
		}
		switch label {
		case "viral":
			summary.Viral = count
		case "successful":
			summary.Successful = count
		case "standard":
			summary.Standard = count
		case "failed":
			summary.Failed = count
		default:
			summary.Unlabeled = count
		}
		summary.Total += count
	}
	return summary, rows.Err()
}

// ListCompetitorVideos returns competitor videos with optional filters.
func (s *SQLiteStore) ListCompetitorVideos(ctx context.Context, uid string, label string, limit int) ([]CompetitorVideo, error) {
	query := `
SELECT bvid, bilibili_uid, title, description, duration, views, likes, coins, favorites, shares, danmaku, comments, publish_time, collected_at, youtube_source_id, label
FROM competitor_videos
WHERE 1=1`
	args := []interface{}{}

	if uid != "" {
		query += ` AND bilibili_uid = ?`
		args = append(args, uid)
	}
	if label != "" {
		if label == "unlabeled" {
			query += ` AND (label IS NULL OR label = '')`
		} else {
			query += ` AND label = ?`
			args = append(args, label)
		}
	}
	query += ` ORDER BY views DESC LIMIT ?`
	args = append(args, limit)

	rows, err := s.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var videos []CompetitorVideo
	for rows.Next() {
		var v CompetitorVideo
		var title, desc, ytSource, label sql.NullString
		var publishTime sql.NullString
		if err := rows.Scan(&v.Bvid, &v.BilibiliUID, &title, &desc, &v.Duration,
			&v.Views, &v.Likes, &v.Coins, &v.Favorites, &v.Shares, &v.Danmaku, &v.Comments,
			&publishTime, &v.CollectedAt, &ytSource, &label); err != nil {
			return nil, err
		}
		v.Title = title.String
		v.Description = desc.String
		v.YoutubeSourceID = ytSource.String
		v.Label = label.String
		if publishTime.Valid {
			t, _ := time.Parse(time.RFC3339, publishTime.String)
			v.PublishTime = &t
		}
		videos = append(videos, v)
	}
	return videos, rows.Err()
}
