package main

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"time"
)

type syncRequest struct {
	ChannelID string `json:"channel_id"`
	Limit     int    `json:"limit"`
}

type syncResponse struct {
	Considered int    `json:"considered"`
	Skipped    int    `json:"skipped"`
	Downloaded int    `json:"downloaded"`
	Uploaded   int    `json:"uploaded"`
	Error      string `json:"error,omitempty"`
}

func serveHTTP(addr string, controller *Controller) error {
	mux := http.NewServeMux()
	mux.HandleFunc("/sync", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		var req syncRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "invalid JSON body", http.StatusBadRequest)
			return
		}
		if req.ChannelID == "" || req.Limit <= 0 {
			http.Error(w, "channel_id and positive limit required", http.StatusBadRequest)
			return
		}

		ctx, cancel := context.WithTimeout(r.Context(), 30*time.Minute)
		defer cancel()

		res, err := controller.SyncChannel(ctx, req.ChannelID, req.Limit)
		payload := syncResponse{
			Considered: res.Considered,
			Skipped:    res.Skipped,
			Downloaded: res.Downloaded,
			Uploaded:   res.Uploaded,
		}
		if err != nil {
			payload.Error = err.Error()
			w.WriteHeader(http.StatusInternalServerError)
		} else {
			w.WriteHeader(http.StatusOK)
		}
		w.Header().Set("Content-Type", "application/json")
		if err := json.NewEncoder(w).Encode(payload); err != nil {
			log.Printf("failed to write response: %v", err)
		}
	})

	log.Printf("controller listening on %s", addr)
	return http.ListenAndServe(addr, mux)
}
