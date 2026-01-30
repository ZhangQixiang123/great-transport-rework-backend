package app

import (
	"context"
	"os"
	"testing"
	"time"
)

func setupTestEngine(t *testing.T) (*RuleEngine, func()) {
	dbPath := t.TempDir() + "/test_rules.db"
	store, err := NewSQLiteStore(dbPath)
	if err != nil {
		t.Fatal(err)
	}

	ctx := context.Background()
	if err := store.EnsureSchema(ctx); err != nil {
		t.Fatal(err)
	}

	engine := NewRuleEngine(store)
	cleanup := func() {
		os.Remove(dbPath)
	}
	return engine, cleanup
}

func TestRuleEngine_SeedDefaultRules(t *testing.T) {
	engine, cleanup := setupTestEngine(t)
	defer cleanup()

	ctx := context.Background()
	if err := engine.SeedDefaultRules(ctx); err != nil {
		t.Fatalf("SeedDefaultRules: %v", err)
	}

	rules, err := engine.Store.ListActiveRules(ctx)
	if err != nil {
		t.Fatalf("ListActiveRules: %v", err)
	}

	if len(rules) != len(DefaultRules) {
		t.Fatalf("got %d rules, want %d", len(rules), len(DefaultRules))
	}

	// Verify seeding is idempotent
	if err := engine.SeedDefaultRules(ctx); err != nil {
		t.Fatalf("second SeedDefaultRules: %v", err)
	}
	rules, _ = engine.Store.ListActiveRules(ctx)
	if len(rules) != len(DefaultRules) {
		t.Fatalf("got %d rules after second seed, want %d", len(rules), len(DefaultRules))
	}
}

func TestRuleEngine_EvaluateMin(t *testing.T) {
	engine, cleanup := setupTestEngine(t)
	defer cleanup()

	ctx := context.Background()

	// Add a min_views rule
	rule := FilterRule{
		RuleName: "min_views",
		RuleType: "min",
		Field:    "view_count",
		Value:    "1000",
		IsActive: true,
		Priority: 100,
	}
	if err := engine.Store.AddRule(ctx, rule); err != nil {
		t.Fatalf("AddRule: %v", err)
	}

	// Add a channel for foreign key
	engine.Store.AddChannel(ctx, Channel{ChannelID: "UC123", URL: "http://example.com", IsActive: true})

	// Test candidate that passes
	now := time.Now().UTC()
	passing := VideoCandidate{
		VideoID:     "vid1",
		ChannelID:   "UC123",
		ViewCount:   1500,
		PublishedAt: &now,
	}
	engine.Store.UpsertCandidate(ctx, passing)

	decision, err := engine.Evaluate(ctx, passing)
	if err != nil {
		t.Fatalf("Evaluate: %v", err)
	}
	if !decision.RulePassed {
		t.Errorf("expected video to pass, got rejected: %s", decision.RejectReason)
	}

	// Test candidate that fails
	failing := VideoCandidate{
		VideoID:     "vid2",
		ChannelID:   "UC123",
		ViewCount:   500,
		PublishedAt: &now,
	}
	engine.Store.UpsertCandidate(ctx, failing)

	decision, err = engine.Evaluate(ctx, failing)
	if err != nil {
		t.Fatalf("Evaluate: %v", err)
	}
	if decision.RulePassed {
		t.Error("expected video to be rejected")
	}
	if decision.RejectRuleName != "min_views" {
		t.Errorf("got reject rule %q, want %q", decision.RejectRuleName, "min_views")
	}
}

func TestRuleEngine_EvaluateMax(t *testing.T) {
	engine, cleanup := setupTestEngine(t)
	defer cleanup()

	ctx := context.Background()

	// Add a max_duration rule
	rule := FilterRule{
		RuleName: "max_duration",
		RuleType: "max",
		Field:    "duration_seconds",
		Value:    "3600",
		IsActive: true,
		Priority: 100,
	}
	if err := engine.Store.AddRule(ctx, rule); err != nil {
		t.Fatalf("AddRule: %v", err)
	}

	engine.Store.AddChannel(ctx, Channel{ChannelID: "UC123", URL: "http://example.com", IsActive: true})

	// Test candidate that passes
	now := time.Now().UTC()
	passing := VideoCandidate{
		VideoID:         "vid1",
		ChannelID:       "UC123",
		DurationSeconds: 1800,
		PublishedAt:     &now,
	}
	engine.Store.UpsertCandidate(ctx, passing)

	decision, err := engine.Evaluate(ctx, passing)
	if err != nil {
		t.Fatalf("Evaluate: %v", err)
	}
	if !decision.RulePassed {
		t.Errorf("expected video to pass, got rejected: %s", decision.RejectReason)
	}

	// Test candidate that fails
	failing := VideoCandidate{
		VideoID:         "vid2",
		ChannelID:       "UC123",
		DurationSeconds: 7200,
		PublishedAt:     &now,
	}
	engine.Store.UpsertCandidate(ctx, failing)

	decision, err = engine.Evaluate(ctx, failing)
	if err != nil {
		t.Fatalf("Evaluate: %v", err)
	}
	if decision.RulePassed {
		t.Error("expected video to be rejected")
	}
	if decision.RejectRuleName != "max_duration" {
		t.Errorf("got reject rule %q, want %q", decision.RejectRuleName, "max_duration")
	}
}

func TestRuleEngine_EvaluateBlocklist(t *testing.T) {
	engine, cleanup := setupTestEngine(t)
	defer cleanup()

	ctx := context.Background()

	// Add a blocklist rule
	rule := FilterRule{
		RuleName: "blocked_categories",
		RuleType: "blocklist",
		Field:    "category",
		Value:    `["News & Politics", "Gaming"]`,
		IsActive: true,
		Priority: 100,
	}
	if err := engine.Store.AddRule(ctx, rule); err != nil {
		t.Fatalf("AddRule: %v", err)
	}

	engine.Store.AddChannel(ctx, Channel{ChannelID: "UC123", URL: "http://example.com", IsActive: true})

	now := time.Now().UTC()

	// Test candidate that passes (not in blocklist)
	passing := VideoCandidate{
		VideoID:     "vid1",
		ChannelID:   "UC123",
		Category:    "Music",
		PublishedAt: &now,
	}
	engine.Store.UpsertCandidate(ctx, passing)

	decision, err := engine.Evaluate(ctx, passing)
	if err != nil {
		t.Fatalf("Evaluate: %v", err)
	}
	if !decision.RulePassed {
		t.Errorf("expected video to pass, got rejected: %s", decision.RejectReason)
	}

	// Test candidate that fails (in blocklist)
	failing := VideoCandidate{
		VideoID:     "vid2",
		ChannelID:   "UC123",
		Category:    "News & Politics",
		PublishedAt: &now,
	}
	engine.Store.UpsertCandidate(ctx, failing)

	decision, err = engine.Evaluate(ctx, failing)
	if err != nil {
		t.Fatalf("Evaluate: %v", err)
	}
	if decision.RulePassed {
		t.Error("expected video to be rejected")
	}
}

func TestRuleEngine_EvaluateAllowlist(t *testing.T) {
	engine, cleanup := setupTestEngine(t)
	defer cleanup()

	ctx := context.Background()

	// Add an allowlist rule
	rule := FilterRule{
		RuleName: "allowed_languages",
		RuleType: "allowlist",
		Field:    "language",
		Value:    `["en", "zh"]`,
		IsActive: true,
		Priority: 100,
	}
	if err := engine.Store.AddRule(ctx, rule); err != nil {
		t.Fatalf("AddRule: %v", err)
	}

	engine.Store.AddChannel(ctx, Channel{ChannelID: "UC123", URL: "http://example.com", IsActive: true})

	now := time.Now().UTC()

	// Test candidate that passes (in allowlist)
	passing := VideoCandidate{
		VideoID:     "vid1",
		ChannelID:   "UC123",
		Language:    "en",
		PublishedAt: &now,
	}
	engine.Store.UpsertCandidate(ctx, passing)

	decision, err := engine.Evaluate(ctx, passing)
	if err != nil {
		t.Fatalf("Evaluate: %v", err)
	}
	if !decision.RulePassed {
		t.Errorf("expected video to pass, got rejected: %s", decision.RejectReason)
	}

	// Test candidate that fails (not in allowlist)
	failing := VideoCandidate{
		VideoID:     "vid2",
		ChannelID:   "UC123",
		Language:    "ja",
		PublishedAt: &now,
	}
	engine.Store.UpsertCandidate(ctx, failing)

	decision, err = engine.Evaluate(ctx, failing)
	if err != nil {
		t.Fatalf("Evaluate: %v", err)
	}
	if decision.RulePassed {
		t.Error("expected video to be rejected")
	}
}

func TestRuleEngine_EvaluateRegex(t *testing.T) {
	engine, cleanup := setupTestEngine(t)
	defer cleanup()

	ctx := context.Background()

	// Add a regex rule to block sponsored content
	rule := FilterRule{
		RuleName: "block_sponsors",
		RuleType: "regex",
		Field:    "title",
		Value:    `(?i)sponsor|ad|promoted`,
		IsActive: true,
		Priority: 100,
	}
	if err := engine.Store.AddRule(ctx, rule); err != nil {
		t.Fatalf("AddRule: %v", err)
	}

	engine.Store.AddChannel(ctx, Channel{ChannelID: "UC123", URL: "http://example.com", IsActive: true})

	now := time.Now().UTC()

	// Test candidate that passes
	passing := VideoCandidate{
		VideoID:     "vid1",
		ChannelID:   "UC123",
		Title:       "My awesome video",
		PublishedAt: &now,
	}
	engine.Store.UpsertCandidate(ctx, passing)

	decision, err := engine.Evaluate(ctx, passing)
	if err != nil {
		t.Fatalf("Evaluate: %v", err)
	}
	if !decision.RulePassed {
		t.Errorf("expected video to pass, got rejected: %s", decision.RejectReason)
	}

	// Test candidate that fails
	failing := VideoCandidate{
		VideoID:     "vid2",
		ChannelID:   "UC123",
		Title:       "This video is Sponsored by XYZ",
		PublishedAt: &now,
	}
	engine.Store.UpsertCandidate(ctx, failing)

	decision, err = engine.Evaluate(ctx, failing)
	if err != nil {
		t.Fatalf("Evaluate: %v", err)
	}
	if decision.RulePassed {
		t.Error("expected video to be rejected")
	}
}

func TestRuleEngine_EvaluateAgeDays(t *testing.T) {
	engine, cleanup := setupTestEngine(t)
	defer cleanup()

	ctx := context.Background()

	// Add an age rule
	rule := FilterRule{
		RuleName: "max_age",
		RuleType: "age_days",
		Field:    "published_at",
		Value:    "7",
		IsActive: true,
		Priority: 100,
	}
	if err := engine.Store.AddRule(ctx, rule); err != nil {
		t.Fatalf("AddRule: %v", err)
	}

	engine.Store.AddChannel(ctx, Channel{ChannelID: "UC123", URL: "http://example.com", IsActive: true})

	// Test candidate that passes (recent)
	recent := time.Now().UTC().Add(-24 * time.Hour)
	passing := VideoCandidate{
		VideoID:     "vid1",
		ChannelID:   "UC123",
		PublishedAt: &recent,
	}
	engine.Store.UpsertCandidate(ctx, passing)

	decision, err := engine.Evaluate(ctx, passing)
	if err != nil {
		t.Fatalf("Evaluate: %v", err)
	}
	if !decision.RulePassed {
		t.Errorf("expected video to pass, got rejected: %s", decision.RejectReason)
	}

	// Test candidate that fails (too old)
	old := time.Now().UTC().Add(-30 * 24 * time.Hour)
	failing := VideoCandidate{
		VideoID:     "vid2",
		ChannelID:   "UC123",
		PublishedAt: &old,
	}
	engine.Store.UpsertCandidate(ctx, failing)

	decision, err = engine.Evaluate(ctx, failing)
	if err != nil {
		t.Fatalf("Evaluate: %v", err)
	}
	if decision.RulePassed {
		t.Error("expected video to be rejected")
	}
}

func TestRuleEngine_EvaluateBatch(t *testing.T) {
	engine, cleanup := setupTestEngine(t)
	defer cleanup()

	ctx := context.Background()

	// Add a simple rule
	rule := FilterRule{
		RuleName: "min_views",
		RuleType: "min",
		Field:    "view_count",
		Value:    "100",
		IsActive: true,
		Priority: 100,
	}
	engine.Store.AddRule(ctx, rule)
	engine.Store.AddChannel(ctx, Channel{ChannelID: "UC123", URL: "http://example.com", IsActive: true})

	now := time.Now().UTC()
	candidates := []VideoCandidate{
		{VideoID: "vid1", ChannelID: "UC123", ViewCount: 200, PublishedAt: &now},
		{VideoID: "vid2", ChannelID: "UC123", ViewCount: 50, PublishedAt: &now},
		{VideoID: "vid3", ChannelID: "UC123", ViewCount: 150, PublishedAt: &now},
		{VideoID: "vid4", ChannelID: "UC123", ViewCount: 75, PublishedAt: &now},
	}

	for _, c := range candidates {
		engine.Store.UpsertCandidate(ctx, c)
	}

	passed, rejected, err := engine.EvaluateBatch(ctx, candidates)
	if err != nil {
		t.Fatalf("EvaluateBatch: %v", err)
	}

	if len(passed) != 2 {
		t.Errorf("got %d passed, want 2", len(passed))
	}
	if len(rejected) != 2 {
		t.Errorf("got %d rejected, want 2", len(rejected))
	}
}

func TestRuleEngine_MultipleRules(t *testing.T) {
	engine, cleanup := setupTestEngine(t)
	defer cleanup()

	ctx := context.Background()

	// Add multiple rules
	rules := []FilterRule{
		{RuleName: "min_views", RuleType: "min", Field: "view_count", Value: "100", IsActive: true, Priority: 100},
		{RuleName: "max_duration", RuleType: "max", Field: "duration_seconds", Value: "600", IsActive: true, Priority: 90},
	}

	for _, r := range rules {
		engine.Store.AddRule(ctx, r)
	}

	engine.Store.AddChannel(ctx, Channel{ChannelID: "UC123", URL: "http://example.com", IsActive: true})

	now := time.Now().UTC()

	// Test candidate that passes both rules
	passing := VideoCandidate{
		VideoID:         "vid1",
		ChannelID:       "UC123",
		ViewCount:       200,
		DurationSeconds: 300,
		PublishedAt:     &now,
	}
	engine.Store.UpsertCandidate(ctx, passing)

	decision, _ := engine.Evaluate(ctx, passing)
	if !decision.RulePassed {
		t.Errorf("expected video to pass both rules")
	}

	// Test candidate that fails min_views (higher priority)
	failViews := VideoCandidate{
		VideoID:         "vid2",
		ChannelID:       "UC123",
		ViewCount:       50,
		DurationSeconds: 300,
		PublishedAt:     &now,
	}
	engine.Store.UpsertCandidate(ctx, failViews)

	decision, _ = engine.Evaluate(ctx, failViews)
	if decision.RulePassed || decision.RejectRuleName != "min_views" {
		t.Errorf("expected rejection by min_views")
	}

	// Test candidate that fails max_duration
	failDuration := VideoCandidate{
		VideoID:         "vid3",
		ChannelID:       "UC123",
		ViewCount:       200,
		DurationSeconds: 1200,
		PublishedAt:     &now,
	}
	engine.Store.UpsertCandidate(ctx, failDuration)

	decision, _ = engine.Evaluate(ctx, failDuration)
	if decision.RulePassed || decision.RejectRuleName != "max_duration" {
		t.Errorf("expected rejection by max_duration, got %s", decision.RejectRuleName)
	}
}

func TestParseRuleFromJSON(t *testing.T) {
	tests := []struct {
		name    string
		json    string
		wantErr bool
	}{
		{
			name:    "valid min rule",
			json:    `{"name":"min_views","type":"min","field":"view_count","value":"1000"}`,
			wantErr: false,
		},
		{
			name:    "valid blocklist rule",
			json:    `{"name":"blocked_cats","type":"blocklist","field":"category","value":"[\"News\"]"}`,
			wantErr: false,
		},
		{
			name:    "missing name",
			json:    `{"type":"min","field":"view_count","value":"1000"}`,
			wantErr: true,
		},
		{
			name:    "missing type",
			json:    `{"name":"test","field":"view_count","value":"1000"}`,
			wantErr: true,
		},
		{
			name:    "invalid type",
			json:    `{"name":"test","type":"invalid","field":"view_count","value":"1000"}`,
			wantErr: true,
		},
		{
			name:    "invalid json",
			json:    `{invalid}`,
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			_, err := ParseRuleFromJSON(tt.json)
			if (err != nil) != tt.wantErr {
				t.Errorf("ParseRuleFromJSON() error = %v, wantErr %v", err, tt.wantErr)
			}
		})
	}
}

func TestStoreRuleMethods(t *testing.T) {
	engine, cleanup := setupTestEngine(t)
	defer cleanup()

	ctx := context.Background()

	// Test AddRule
	rule := FilterRule{
		RuleName: "test_rule",
		RuleType: "min",
		Field:    "view_count",
		Value:    "100",
		IsActive: true,
		Priority: 50,
	}
	if err := engine.Store.AddRule(ctx, rule); err != nil {
		t.Fatalf("AddRule: %v", err)
	}

	// Test GetRule
	got, err := engine.Store.GetRule(ctx, "test_rule")
	if err != nil {
		t.Fatalf("GetRule: %v", err)
	}
	if got == nil {
		t.Fatal("GetRule returned nil")
	}
	if got.Value != "100" {
		t.Errorf("got value %q, want %q", got.Value, "100")
	}

	// Test UpdateRule
	if err := engine.Store.UpdateRule(ctx, "test_rule", "200"); err != nil {
		t.Fatalf("UpdateRule: %v", err)
	}
	got, _ = engine.Store.GetRule(ctx, "test_rule")
	if got.Value != "200" {
		t.Errorf("got value %q after update, want %q", got.Value, "200")
	}

	// Test ListAllRules
	rules, err := engine.Store.ListAllRules(ctx)
	if err != nil {
		t.Fatalf("ListAllRules: %v", err)
	}
	if len(rules) != 1 {
		t.Errorf("got %d rules, want 1", len(rules))
	}

	// Test DeleteRule
	if err := engine.Store.DeleteRule(ctx, "test_rule"); err != nil {
		t.Fatalf("DeleteRule: %v", err)
	}
	got, _ = engine.Store.GetRule(ctx, "test_rule")
	if got != nil {
		t.Error("rule should be deleted")
	}
}

func TestListFilteredAndRejectedCandidates(t *testing.T) {
	engine, cleanup := setupTestEngine(t)
	defer cleanup()

	ctx := context.Background()

	// Add a rule
	rule := FilterRule{
		RuleName: "min_views",
		RuleType: "min",
		Field:    "view_count",
		Value:    "100",
		IsActive: true,
		Priority: 100,
	}
	engine.Store.AddRule(ctx, rule)
	engine.Store.AddChannel(ctx, Channel{ChannelID: "UC123", URL: "http://example.com", IsActive: true})

	now := time.Now().UTC()
	candidates := []VideoCandidate{
		{VideoID: "vid1", ChannelID: "UC123", Title: "Good Video", ViewCount: 200, PublishedAt: &now},
		{VideoID: "vid2", ChannelID: "UC123", Title: "Low Views", ViewCount: 50, PublishedAt: &now},
	}

	for _, c := range candidates {
		engine.Store.UpsertCandidate(ctx, c)
	}

	// Evaluate candidates
	engine.EvaluateBatch(ctx, candidates)

	// Test ListFilteredCandidates
	filtered, err := engine.Store.ListFilteredCandidates(ctx, 10)
	if err != nil {
		t.Fatalf("ListFilteredCandidates: %v", err)
	}
	if len(filtered) != 1 {
		t.Errorf("got %d filtered candidates, want 1", len(filtered))
	}
	if len(filtered) > 0 && filtered[0].VideoID != "vid1" {
		t.Errorf("expected vid1 to pass")
	}

	// Test ListRejectedCandidates
	rejected, err := engine.Store.ListRejectedCandidates(ctx, 10)
	if err != nil {
		t.Fatalf("ListRejectedCandidates: %v", err)
	}
	if len(rejected) != 1 {
		t.Errorf("got %d rejected candidates, want 1", len(rejected))
	}
	if len(rejected) > 0 && rejected[0].VideoID != "vid2" {
		t.Errorf("expected vid2 to be rejected")
	}
}
