package app

import (
	"context"
	"database/sql"
	"fmt"
	"time"

	_ "modernc.org/sqlite"
)

type SQLiteStore struct {
	db *sql.DB
}

func NewSQLiteStore(path string) (*SQLiteStore, error) {
	db, err := sql.Open("sqlite", path+"?_pragma=busy_timeout(5000)&_pragma=journal_mode(WAL)")
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
		// Upload jobs table (HTTP API)
		`CREATE TABLE IF NOT EXISTS upload_jobs (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			video_id TEXT NOT NULL,
			status TEXT NOT NULL DEFAULT 'pending',
			title TEXT,
			description TEXT,
			tags TEXT,
			bilibili_bvid TEXT,
			download_files TEXT,
			subtitle_status TEXT NOT NULL DEFAULT 'pending',
			error_message TEXT,
			created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		);`,
		`CREATE INDEX IF NOT EXISTS idx_upload_jobs_video ON upload_jobs(video_id);`,
		`CREATE INDEX IF NOT EXISTS idx_upload_jobs_status ON upload_jobs(status);`,
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

	// Migration: Add download_files and subtitle_status columns to upload_jobs
	if err := s.migrateUploadJobsSubtitle(ctx); err != nil {
		return err
	}

	// Migration: Add subtitle_draft column
	if err := s.migrateAddSubtitleDraft(ctx); err != nil {
		return err
	}

	return nil
}

// migrateAddBilibiliBvid adds the bilibili_bvid column to existing uploads table.
func (s *SQLiteStore) migrateAddBilibiliBvid(ctx context.Context) error {
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

// migrateUploadJobsSubtitle adds download_files and subtitle_status columns to upload_jobs.
func (s *SQLiteStore) migrateUploadJobsSubtitle(ctx context.Context) error {
	rows, err := s.db.QueryContext(ctx, `PRAGMA table_info(upload_jobs)`)
	if err != nil {
		return err
	}
	defer rows.Close()

	hasDownloadFiles := false
	hasSubtitleStatus := false
	for rows.Next() {
		var cid int
		var name, ctype string
		var notnull, pk int
		var dflt sql.NullString
		if err := rows.Scan(&cid, &name, &ctype, &notnull, &dflt, &pk); err != nil {
			return err
		}
		if name == "download_files" {
			hasDownloadFiles = true
		}
		if name == "subtitle_status" {
			hasSubtitleStatus = true
		}
	}

	if !hasDownloadFiles {
		if _, err := s.db.ExecContext(ctx, `ALTER TABLE upload_jobs ADD COLUMN download_files TEXT`); err != nil {
			return err
		}
	}
	if !hasSubtitleStatus {
		if _, err := s.db.ExecContext(ctx, `ALTER TABLE upload_jobs ADD COLUMN subtitle_status TEXT NOT NULL DEFAULT 'pending'`); err != nil {
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

func nullableString(s string) interface{} {
	if s == "" {
		return nil
	}
	return s
}

// scanner is an interface matching *sql.Row and *sql.Rows for scanning.
type scanner interface {
	Scan(dest ...interface{}) error
}

// Upload Jobs Methods

// CreateUploadJob inserts a new upload job and returns its ID.
func (s *SQLiteStore) CreateUploadJob(ctx context.Context, videoID, title, desc, tags string) (int64, error) {
	now := time.Now().UTC()
	result, err := s.db.ExecContext(ctx, `
INSERT INTO upload_jobs (video_id, status, title, description, tags, created_at, updated_at)
VALUES (?, 'pending', ?, ?, ?, ?, ?)`, videoID, title, desc, tags, now, now)
	if err != nil {
		return 0, err
	}
	return result.LastInsertId()
}

// UpdateUploadJobStatus updates the status, bvid, and error message for an upload job.
func (s *SQLiteStore) UpdateUploadJobStatus(ctx context.Context, id int64, status, bvid, errorMsg string) error {
	_, err := s.db.ExecContext(ctx, `
UPDATE upload_jobs SET status = ?, bilibili_bvid = ?, error_message = ?, updated_at = ?
WHERE id = ?`, status, nullableString(bvid), nullableString(errorMsg), time.Now().UTC(), id)
	return err
}

// migrateAddSubtitleDraft adds the subtitle_draft column to upload_jobs.
func (s *SQLiteStore) migrateAddSubtitleDraft(ctx context.Context) error {
	rows, err := s.db.QueryContext(ctx, `PRAGMA table_info(upload_jobs)`)
	if err != nil {
		return err
	}
	defer rows.Close()

	for rows.Next() {
		var cid int
		var name, typ string
		var notNull, pk int
		var dflt sql.NullString
		if err := rows.Scan(&cid, &name, &typ, &notNull, &dflt, &pk); err != nil {
			return err
		}
		if name == "subtitle_draft" {
			return nil // already exists
		}
	}
	_, err = s.db.ExecContext(ctx, `ALTER TABLE upload_jobs ADD COLUMN subtitle_draft TEXT`)
	return err
}

// SaveSubtitleDraft saves generated subtitle data (JSON) for review.
func (s *SQLiteStore) SaveSubtitleDraft(ctx context.Context, id int64, draftJSON string) error {
	_, err := s.db.ExecContext(ctx, `
UPDATE upload_jobs SET subtitle_draft = ?, updated_at = ? WHERE id = ?`,
		draftJSON, time.Now().UTC(), id)
	return err
}

// GetSubtitleDraft retrieves the subtitle draft JSON for a job.
func (s *SQLiteStore) GetSubtitleDraft(ctx context.Context, id int64) (string, error) {
	var draft sql.NullString
	err := s.db.QueryRowContext(ctx, `SELECT subtitle_draft FROM upload_jobs WHERE id = ?`, id).Scan(&draft)
	if err != nil {
		return "", err
	}
	if !draft.Valid || draft.String == "" {
		return "", fmt.Errorf("no subtitle draft for job %d", id)
	}
	return draft.String, nil
}

// UpdateUploadJobFiles stores the downloaded file paths (JSON array) for a job.
func (s *SQLiteStore) UpdateUploadJobFiles(ctx context.Context, id int64, filesJSON string) error {
	_, err := s.db.ExecContext(ctx, `
UPDATE upload_jobs SET download_files = ?, updated_at = ? WHERE id = ?`,
		filesJSON, time.Now().UTC(), id)
	return err
}

// UpdateSubtitleStatus updates the subtitle_status for a job.
func (s *SQLiteStore) UpdateSubtitleStatus(ctx context.Context, id int64, status string) error {
	_, err := s.db.ExecContext(ctx, `
UPDATE upload_jobs SET subtitle_status = ?, updated_at = ? WHERE id = ?`,
		status, time.Now().UTC(), id)
	return err
}

// uploadJobColumns is the shared column list for upload_jobs queries.
const uploadJobColumns = `id, video_id, status, title, description, tags, bilibili_bvid, download_files, subtitle_status, error_message, created_at, updated_at`

// scanUploadJob scans a single upload job row.
func scanUploadJob(row scanner) (UploadJob, error) {
	var job UploadJob
	var title, desc, tags, bvid, dlFiles, subStatus, errMsg sql.NullString
	err := row.Scan(&job.ID, &job.VideoID, &job.Status, &title, &desc, &tags, &bvid, &dlFiles, &subStatus, &errMsg, &job.CreatedAt, &job.UpdatedAt)
	if err != nil {
		return job, err
	}
	job.Title = title.String
	job.Description = desc.String
	job.Tags = tags.String
	job.BilibiliBvid = bvid.String
	job.DownloadFiles = dlFiles.String
	job.SubtitleStatus = subStatus.String
	if job.SubtitleStatus == "" {
		job.SubtitleStatus = "pending"
	}
	job.ErrorMessage = errMsg.String
	return job, nil
}

// scanUploadJobs scans multiple upload job rows.
func scanUploadJobs(rows *sql.Rows) ([]UploadJob, error) {
	var jobs []UploadJob
	for rows.Next() {
		job, err := scanUploadJob(rows)
		if err != nil {
			return nil, err
		}
		jobs = append(jobs, job)
	}
	return jobs, rows.Err()
}

// GetNextPendingJob retrieves the oldest pending upload job.
func (s *SQLiteStore) GetNextPendingJob(ctx context.Context) (*UploadJob, error) {
	row := s.db.QueryRowContext(ctx, `
SELECT `+uploadJobColumns+` FROM upload_jobs WHERE status = 'pending' ORDER BY id ASC LIMIT 1`)

	job, err := scanUploadJob(row)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &job, nil
}

// ListRecentUploadJobs returns the most recent upload jobs.
func (s *SQLiteStore) ListRecentUploadJobs(ctx context.Context, limit int) ([]UploadJob, error) {
	rows, err := s.db.QueryContext(ctx, `
SELECT `+uploadJobColumns+` FROM upload_jobs ORDER BY id DESC LIMIT ?`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return scanUploadJobs(rows)
}

// GetAllUploadedVideoIDs returns all video IDs that have been uploaded or are in non-failed jobs.
func (s *SQLiteStore) GetAllUploadedVideoIDs(ctx context.Context) ([]string, error) {
	rows, err := s.db.QueryContext(ctx, `
SELECT video_id FROM uploads
UNION
SELECT video_id FROM upload_jobs WHERE status != 'failed'`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var ids []string
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		ids = append(ids, id)
	}
	return ids, rows.Err()
}

// FindActiveUploadJob finds a non-failed upload job for a given video ID.
func (s *SQLiteStore) FindActiveUploadJob(ctx context.Context, videoID string) (*UploadJob, error) {
	row := s.db.QueryRowContext(ctx, `
SELECT `+uploadJobColumns+` FROM upload_jobs WHERE video_id = ? AND status != 'failed' ORDER BY id DESC LIMIT 1`, videoID)

	job, err := scanUploadJob(row)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &job, nil
}

// GetUploadJob retrieves an upload job by ID.
func (s *SQLiteStore) GetUploadJob(ctx context.Context, id int64) (*UploadJob, error) {
	row := s.db.QueryRowContext(ctx, `
SELECT `+uploadJobColumns+` FROM upload_jobs WHERE id = ?`, id)

	job, err := scanUploadJob(row)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &job, nil
}
