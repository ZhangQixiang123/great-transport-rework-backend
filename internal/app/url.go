package app

import (
	"fmt"
	"strings"
)

func videoURL(input string) string {
	if looksLikeURL(input) {
		return input
	}
	return fmt.Sprintf("https://www.youtube.com/watch?v=%s", input)
}

func looksLikeURL(input string) bool {
	return strings.HasPrefix(input, "http://") || strings.HasPrefix(input, "https://")
}
