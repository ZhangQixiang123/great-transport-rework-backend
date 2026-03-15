package app

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

type uploadRequest struct {
	VideoID     string `json:"video_id"`
	Title       string `json:"title"`
	Description string `json:"description"`
	Tags        string `json:"tags"`
}

type uploadResponse struct {
	JobID        int64  `json:"job_id"`
	Status       string `json:"status"`
	BilibiliBvid string `json:"bilibili_bvid,omitempty"`
	Error        string `json:"error,omitempty"`
}

func ServeHTTP(addr string, controller *Controller) error {
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

	mux.HandleFunc("/upload", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		var req uploadRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "invalid JSON body", http.StatusBadRequest)
			return
		}
		if req.VideoID == "" {
			http.Error(w, "video_id is required", http.StatusBadRequest)
			return
		}

		// Create job in DB
		jobID, err := controller.Store.CreateUploadJob(r.Context(), req.VideoID, req.Title, req.Description, req.Tags)
		if err != nil {
			resp := uploadResponse{Status: "failed", Error: "failed to create job: " + err.Error()}
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(resp)
			return
		}

		job := UploadJob{
			ID:          jobID,
			VideoID:     req.VideoID,
			Status:      "pending",
			Title:       req.Title,
			Description: req.Description,
			Tags:        req.Tags,
		}

		// Run upload synchronously (long timeout for download+upload)
		ctx, cancel := context.WithTimeout(r.Context(), 30*time.Minute)
		defer cancel()

		job, uploadErr := controller.UploadVideo(ctx, job)

		resp := uploadResponse{
			JobID:        jobID,
			Status:       job.Status,
			BilibiliBvid: job.BilibiliBvid,
		}
		if uploadErr != nil {
			resp.Error = uploadErr.Error()
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusInternalServerError)
		} else {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusOK)
		}
		if err := json.NewEncoder(w).Encode(resp); err != nil {
			log.Printf("failed to write upload response: %v", err)
		}
	})

	log.Printf("controller listening on %s", addr)
	return http.ListenAndServe(addr, mux)
}
