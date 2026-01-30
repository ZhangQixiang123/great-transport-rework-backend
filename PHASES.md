# Great Transport - AI-Powered Video Discovery System

## Overview

This document outlines the phased implementation plan for extending the Great Transport backend with AI-powered video discovery and selection capabilities.

---

## Phase 1: Foundation (Completed)

**Goal**: Automatically discover and store video candidates with full metadata

### Database Schema

```sql
-- Channels to monitor
CREATE TABLE IF NOT EXISTS channels (
    channel_id TEXT PRIMARY KEY,
    name TEXT,
    url TEXT NOT NULL,
    subscriber_count INTEGER,
    video_count INTEGER,
    last_scanned_at TIMESTAMP,
    scan_frequency_hours INTEGER DEFAULT 6,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Discovered video candidates
CREATE TABLE IF NOT EXISTS video_candidates (
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
);
```

### New CLI Commands

| Command | Description |
|---------|-------------|
| `--add-channel URL` | Add a channel to the watchlist |
| `--remove-channel ID` | Remove a channel from the watchlist |
| `--list-channels` | List all watched channels |
| `--scan` | Scan all active channels for new videos |
| `--scan-channel ID` | Scan a specific channel |
| `--list-candidates` | List discovered video candidates |
| `--candidate-limit N` | Limit for candidate listing (default 20) |

### Files Changed

| File | Action | Description |
|------|--------|-------------|
| `internal/app/store.go` | Modified | Extended schema, added repository methods |
| `internal/app/downloader.go` | Modified | Added metadata extraction methods |
| `internal/app/scanner.go` | Created | Scanner service for channel discovery |
| `internal/app/helpers.go` | Created | Date parsing, metric computation |
| `internal/app/repository.go` | Created | Channel and VideoCandidate structs |
| `cmd/yttransfer/main.go` | Modified | New CLI flags and mode handling |

### Verification

```bash
# Add a channel
./yt-transfer --add-channel "https://www.youtube.com/@SomeChannel"

# List channels
./yt-transfer --list-channels

# Scan the channel
./yt-transfer --scan-channel UC_xxxxx --limit 10

# List discovered candidates
./yt-transfer --list-candidates --candidate-limit 20

# Verify database
sqlite3 metadata.db "SELECT COUNT(*) FROM video_candidates;"
```

---

## Phase 2: Rule Engine (Completed)

**Goal**: Implement configurable rule-based filtering

Rule filtering acts as a gatekeeper in the decision pipeline:
```
Stage 1: Rule Filter (THIS PHASE) → Stage 2: ML Scorer → Stage 3: LLM Agent
```

### New Components

1. **Filter Rules Table** - Store configurable filter rules
2. **Rule Engine** - Evaluate candidates against rules
3. **Decision Logging** - Track rule evaluations and rejections
4. **Default Rules** - Sensible built-in rules for common filtering

### Rule Types

| Type | Description | Example |
|------|-------------|---------|
| `min` | Minimum threshold | min_views = 1000 |
| `max` | Maximum threshold | max_duration = 3600 |
| `blocklist` | Reject if in list | blocked_categories = ["News"] |
| `allowlist` | Accept only if in list | allowed_languages = ["en", "zh"] |
| `regex` | Reject if pattern matches | title_blocklist = "(?i)sponsor" |
| `age_days` | Max age since publish | max_age = 30 |

### Schema Additions

```sql
-- Rule filter configuration
CREATE TABLE IF NOT EXISTS filter_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_name TEXT NOT NULL UNIQUE,
    rule_type TEXT NOT NULL,  -- 'min', 'max', 'blocklist', 'allowlist', 'regex', 'age_days'
    field TEXT NOT NULL,      -- 'view_count', 'duration_seconds', 'category', 'title', etc.
    value TEXT NOT NULL,      -- JSON value (number, array, or string pattern)
    is_active INTEGER DEFAULT 1,
    priority INTEGER DEFAULT 0,  -- Higher priority rules evaluated first
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Decision log for rule evaluations
CREATE TABLE IF NOT EXISTS rule_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    rule_passed INTEGER NOT NULL,  -- 1 = passed all rules, 0 = rejected
    reject_rule_name TEXT,         -- Which rule rejected it (if any)
    reject_reason TEXT,            -- Human-readable reason
    evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (video_id) REFERENCES video_candidates(video_id)
);
```

### CLI Commands

| Command | Description |
|---------|-------------|
| `--list-rules` | List all filter rules |
| `--set-rule NAME=VALUE` | Set/update a rule value |
| `--add-rule JSON` | Add a custom rule |
| `--remove-rule NAME` | Remove a rule |
| `--filter` | Run rule filter on pending candidates |
| `--list-filtered` | List candidates that passed filtering |
| `--list-rejected` | List rejected candidates with reasons |

### Files Changed

| File | Action | Description |
|------|--------|-------------|
| `internal/app/store.go` | Modified | Added filter_rules and rule_decisions tables |
| `internal/app/rules.go` | Created | Rule engine implementation |
| `internal/app/rules_test.go` | Created | Tests for rule engine |
| `internal/app/scanner.go` | Modified | Optional auto-filter integration |
| `cmd/yttransfer/main.go` | Modified | New rule management CLI flags |

### Verification

```bash
# List default rules
./yt-transfer --list-rules

# Set a rule value
./yt-transfer --set-rule "min_views=5000"

# Add a custom rule
./yt-transfer --add-rule '{"name":"block_sponsors","type":"regex","field":"title","value":"(?i)sponsor|ad"}'

# Remove a rule
./yt-transfer --remove-rule "block_sponsors"

# Run filtering
./yt-transfer --filter --limit 100

# Check filtered results
./yt-transfer --list-filtered
./yt-transfer --list-rejected

# Verify database
sqlite3 metadata.db "SELECT * FROM filter_rules;"
sqlite3 metadata.db "SELECT rule_passed, COUNT(*) FROM rule_decisions GROUP BY rule_passed;"
```

---

## Phase 3: AI Selection Engine (Planned)

**Goal**: Implement AI-powered video selection using Claude API

### New Components

1. **Selection Criteria Table** - Store selection rules and preferences
2. **AI Selector Service** - Evaluate candidates using Claude API
3. **Selection History** - Track AI decisions for learning/auditing
4. **Batch Processing** - Process candidates in configurable batches

### Proposed Schema Additions

```sql
-- Selection criteria and rules
CREATE TABLE IF NOT EXISTS selection_criteria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    prompt_template TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- AI selection decisions
CREATE TABLE IF NOT EXISTS selections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    score REAL,
    decision TEXT CHECK(decision IN ('approve', 'reject', 'review')),
    reasoning TEXT,
    model_used TEXT,
    tokens_used INTEGER,
    selected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (video_id) REFERENCES video_candidates(video_id)
);

-- Selection queue for approved videos
CREATE TABLE IF NOT EXISTS selection_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL UNIQUE,
    priority INTEGER DEFAULT 0,
    scheduled_for TIMESTAMP,
    status TEXT CHECK(status IN ('pending', 'processing', 'completed', 'failed')),
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (video_id) REFERENCES video_candidates(video_id)
);
```

### Proposed CLI Commands

| Command | Description |
|---------|-------------|
| `--select` | Run AI selection on pending candidates |
| `--select-limit N` | Max candidates to evaluate per run |
| `--list-queue` | Show approved videos in queue |
| `--process-queue` | Process queued videos (download + upload) |

---

## Phase 4: Scheduling & Automation (Future)

**Goal**: Automated scheduling and continuous operation

### Features

- Cron-like scheduling for scans and selections
- Rate limiting and quota management
- Background daemon mode
- Webhook notifications
- Dashboard/API for monitoring

---

## Phase 5: Analytics & Optimization (Future)

**Goal**: Learn from performance and optimize selection

### Features

- Track upload performance (views, engagement on target platform)
- Feedback loop to improve selection criteria
- A/B testing different selection strategies
- Performance reports and insights
