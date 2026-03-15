package app

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// RuleEngine evaluates video candidates against configurable filter rules.
type RuleEngine struct {
	Store *SQLiteStore
}

// NewRuleEngine creates a new rule engine.
func NewRuleEngine(store *SQLiteStore) *RuleEngine {
	return &RuleEngine{Store: store}
}

// DefaultRules defines sensible default filtering rules.
var DefaultRules = []FilterRule{
	{RuleName: "min_views", RuleType: "min", Field: "view_count", Value: "1000", IsActive: true, Priority: 100},
	{RuleName: "max_age_days", RuleType: "age_days", Field: "published_at", Value: "30", IsActive: true, Priority: 90},
	{RuleName: "min_duration", RuleType: "min", Field: "duration_seconds", Value: "60", IsActive: true, Priority: 80},
	{RuleName: "max_duration", RuleType: "max", Field: "duration_seconds", Value: "3600", IsActive: true, Priority: 80},
	{RuleName: "blocked_categories", RuleType: "blocklist", Field: "category", Value: `["News & Politics"]`, IsActive: true, Priority: 70},
}

// SeedDefaultRules adds default rules if they don't exist.
func (e *RuleEngine) SeedDefaultRules(ctx context.Context) error {
	for _, rule := range DefaultRules {
		existing, err := e.Store.GetRule(ctx, rule.RuleName)
		if err != nil {
			return err
		}
		if existing == nil {
			if err := e.Store.AddRule(ctx, rule); err != nil {
				return err
			}
		}
	}
	return nil
}

// Evaluate checks a single candidate against all active rules.
func (e *RuleEngine) Evaluate(ctx context.Context, candidate VideoCandidate) (*RuleDecision, error) {
	rules, err := e.Store.ListActiveRules(ctx)
	if err != nil {
		return nil, err
	}

	for _, rule := range rules {
		passed, reason := e.evaluateRule(rule, candidate)
		if !passed {
			decision := RuleDecision{
				VideoID:        candidate.VideoID,
				RulePassed:     false,
				RejectRuleName: rule.RuleName,
				RejectReason:   reason,
				EvaluatedAt:    time.Now().UTC(),
			}
			if err := e.Store.RecordRuleDecision(ctx, decision); err != nil {
				return nil, err
			}
			return &decision, nil
		}
	}

	// Passed all rules
	decision := RuleDecision{
		VideoID:     candidate.VideoID,
		RulePassed:  true,
		EvaluatedAt: time.Now().UTC(),
	}
	if err := e.Store.RecordRuleDecision(ctx, decision); err != nil {
		return nil, err
	}
	return &decision, nil
}

// EvaluateBatch evaluates multiple candidates and returns passed/rejected lists.
func (e *RuleEngine) EvaluateBatch(ctx context.Context, candidates []VideoCandidate) (passed, rejected []VideoCandidate, err error) {
	for _, c := range candidates {
		decision, err := e.Evaluate(ctx, c)
		if err != nil {
			return nil, nil, err
		}
		if decision.RulePassed {
			passed = append(passed, c)
		} else {
			rejected = append(rejected, c)
		}
	}
	return passed, rejected, nil
}

// FilterPendingCandidates fetches unevaluated candidates and evaluates them.
func (e *RuleEngine) FilterPendingCandidates(ctx context.Context, limit int) (passed, rejected []VideoCandidate, err error) {
	candidates, err := e.Store.ListUnevaluatedCandidates(ctx, limit)
	if err != nil {
		return nil, nil, err
	}
	return e.EvaluateBatch(ctx, candidates)
}

// evaluateRule checks a single rule against a candidate.
func (e *RuleEngine) evaluateRule(rule FilterRule, candidate VideoCandidate) (bool, string) {
	switch rule.RuleType {
	case "min":
		return e.evaluateMin(rule, candidate)
	case "max":
		return e.evaluateMax(rule, candidate)
	case "blocklist":
		return e.evaluateBlocklist(rule, candidate)
	case "allowlist":
		return e.evaluateAllowlist(rule, candidate)
	case "regex":
		return e.evaluateRegex(rule, candidate)
	case "age_days":
		return e.evaluateAgeDays(rule, candidate)
	default:
		// Unknown rule type, pass by default
		return true, ""
	}
}

// getNumericField returns the numeric value of a candidate field, and whether the field is known.
func getNumericField(field string, candidate VideoCandidate) (float64, bool) {
	switch field {
	case "view_count":
		return float64(candidate.ViewCount), true
	case "like_count":
		return float64(candidate.LikeCount), true
	case "comment_count":
		return float64(candidate.CommentCount), true
	case "duration_seconds":
		return float64(candidate.DurationSeconds), true
	case "view_velocity":
		return candidate.ViewVelocity, true
	case "engagement_rate":
		return candidate.EngagementRate, true
	default:
		return 0, false
	}
}

// getStringField returns the string value of a candidate field, and whether the field is known.
func getStringField(field string, candidate VideoCandidate) (string, bool) {
	switch field {
	case "category":
		return candidate.Category, true
	case "language":
		return candidate.Language, true
	case "channel_id":
		return candidate.ChannelID, true
	case "title":
		return candidate.Title, true
	case "description":
		return candidate.Description, true
	default:
		return "", false
	}
}

// evaluateMin checks if a numeric field meets minimum threshold.
func (e *RuleEngine) evaluateMin(rule FilterRule, candidate VideoCandidate) (bool, string) {
	threshold, err := strconv.ParseFloat(rule.Value, 64)
	if err != nil {
		return true, ""
	}

	actual, ok := getNumericField(rule.Field, candidate)
	if !ok {
		return true, ""
	}

	if actual < threshold {
		return false, fmt.Sprintf("%s (%v) below minimum (%v)", rule.Field, actual, threshold)
	}
	return true, ""
}

// evaluateMax checks if a numeric field is below maximum threshold.
func (e *RuleEngine) evaluateMax(rule FilterRule, candidate VideoCandidate) (bool, string) {
	threshold, err := strconv.ParseFloat(rule.Value, 64)
	if err != nil {
		return true, ""
	}

	actual, ok := getNumericField(rule.Field, candidate)
	if !ok {
		return true, ""
	}

	if actual > threshold {
		return false, fmt.Sprintf("%s (%v) exceeds maximum (%v)", rule.Field, actual, threshold)
	}
	return true, ""
}

// evaluateBlocklist rejects if field value is in the blocklist.
func (e *RuleEngine) evaluateBlocklist(rule FilterRule, candidate VideoCandidate) (bool, string) {
	var blocklist []string
	if err := json.Unmarshal([]byte(rule.Value), &blocklist); err != nil {
		return true, ""
	}

	fieldValue, ok := getStringField(rule.Field, candidate)
	if !ok {
		return true, ""
	}

	fieldLower := strings.ToLower(fieldValue)
	for _, blocked := range blocklist {
		if strings.ToLower(blocked) == fieldLower {
			return false, fmt.Sprintf("%s '%s' is blocked", rule.Field, fieldValue)
		}
	}
	return true, ""
}

// evaluateAllowlist accepts only if field value is in the allowlist.
func (e *RuleEngine) evaluateAllowlist(rule FilterRule, candidate VideoCandidate) (bool, string) {
	var allowlist []string
	if err := json.Unmarshal([]byte(rule.Value), &allowlist); err != nil {
		return true, ""
	}

	if len(allowlist) == 0 {
		return true, ""
	}

	fieldValue, ok := getStringField(rule.Field, candidate)
	if !ok {
		return true, ""
	}

	fieldLower := strings.ToLower(fieldValue)
	for _, allowed := range allowlist {
		if strings.ToLower(allowed) == fieldLower {
			return true, ""
		}
	}
	return false, fmt.Sprintf("%s '%s' is not in allowed list", rule.Field, fieldValue)
}

// evaluateRegex rejects if field matches the regex pattern.
func (e *RuleEngine) evaluateRegex(rule FilterRule, candidate VideoCandidate) (bool, string) {
	re, err := regexp.Compile(rule.Value)
	if err != nil {
		return true, ""
	}

	fieldValue, ok := getStringField(rule.Field, candidate)
	if !ok {
		return true, ""
	}

	if re.MatchString(fieldValue) {
		return false, fmt.Sprintf("%s matches blocked pattern '%s'", rule.Field, rule.Value)
	}
	return true, ""
}

// evaluateAgeDays rejects if video is older than specified days.
func (e *RuleEngine) evaluateAgeDays(rule FilterRule, candidate VideoCandidate) (bool, string) {
	maxDays, err := strconv.Atoi(rule.Value)
	if err != nil {
		return true, "" // Invalid value, pass
	}

	if candidate.PublishedAt == nil {
		return true, "" // No publish date, pass
	}

	age := time.Since(*candidate.PublishedAt)
	ageDays := int(age.Hours() / 24)

	if ageDays > maxDays {
		return false, fmt.Sprintf("video age (%d days) exceeds maximum (%d days)", ageDays, maxDays)
	}
	return true, ""
}

// ParseRuleJSON parses a JSON rule definition for --add-rule.
type RuleJSON struct {
	Name     string `json:"name"`
	Type     string `json:"type"`
	Field    string `json:"field"`
	Value    string `json:"value"`
	Priority int    `json:"priority,omitempty"`
}

// ParseRuleFromJSON parses a JSON string into a FilterRule.
func ParseRuleFromJSON(jsonStr string) (*FilterRule, error) {
	var rj RuleJSON
	if err := json.Unmarshal([]byte(jsonStr), &rj); err != nil {
		return nil, fmt.Errorf("invalid JSON: %w", err)
	}

	if rj.Name == "" {
		return nil, fmt.Errorf("rule name is required")
	}
	if rj.Type == "" {
		return nil, fmt.Errorf("rule type is required")
	}
	if rj.Field == "" {
		return nil, fmt.Errorf("rule field is required")
	}
	if rj.Value == "" {
		return nil, fmt.Errorf("rule value is required")
	}

	validTypes := map[string]bool{
		"min": true, "max": true, "blocklist": true,
		"allowlist": true, "regex": true, "age_days": true,
	}
	if !validTypes[rj.Type] {
		return nil, fmt.Errorf("invalid rule type: %s (must be min, max, blocklist, allowlist, regex, or age_days)", rj.Type)
	}

	return &FilterRule{
		RuleName: rj.Name,
		RuleType: rj.Type,
		Field:    rj.Field,
		Value:    rj.Value,
		IsActive: true,
		Priority: rj.Priority,
	}, nil
}
