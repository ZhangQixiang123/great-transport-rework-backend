package main

import (
	"context"
	"database/sql"
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
	_, err := s.db.ExecContext(ctx, `
CREATE TABLE IF NOT EXISTS uploads (
	video_id TEXT PRIMARY KEY,
	channel_id TEXT NOT NULL,
	uploaded_at TIMESTAMP NOT NULL
);`)
	return err
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
