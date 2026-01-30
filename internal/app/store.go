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
	}
	for _, stmt := range statements {
		if _, err := s.db.ExecContext(ctx, stmt); err != nil {
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
	if channelID == "" {
		channelID = "unknown"
	}
	_, err := s.db.ExecContext(ctx, `
INSERT INTO uploads (video_id, channel_id, uploaded_at)
VALUES (?, ?, ?)
ON CONFLICT(video_id) DO UPDATE SET
	channel_id = excluded.channel_id,
	uploaded_at = excluded.uploaded_at;`, videoID, channelID, time.Now().UTC())
	return err
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
