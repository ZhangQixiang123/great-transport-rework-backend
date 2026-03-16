package app

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"strconv"
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

type jobStatusResponse struct {
	JobID          int64  `json:"job_id"`
	VideoID        string `json:"video_id"`
	Status         string `json:"status"`
	Title          string `json:"title,omitempty"`
	BilibiliBvid   string `json:"bilibili_bvid,omitempty"`
	DownloadFiles  string `json:"download_files,omitempty"`
	SubtitleStatus string `json:"subtitle_status"`
	ErrorMessage   string `json:"error_message,omitempty"`
	CreatedAt      string `json:"created_at"`
	UpdatedAt      string `json:"updated_at"`
}

func ServeHTTP(addr string, controller *Controller, queue *JobQueue) error {
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

		// Notify the queue worker
		queue.Enqueue()

		// Return 202 Accepted immediately
		resp := uploadResponse{
			JobID:  jobID,
			Status: "pending",
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusAccepted)
		if err := json.NewEncoder(w).Encode(resp); err != nil {
			log.Printf("failed to write upload response: %v", err)
		}
	})

	mux.HandleFunc("/upload/status", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		idStr := r.URL.Query().Get("id")
		if idStr == "" {
			http.Error(w, "id query parameter is required", http.StatusBadRequest)
			return
		}
		id, err := strconv.ParseInt(idStr, 10, 64)
		if err != nil {
			http.Error(w, "invalid id", http.StatusBadRequest)
			return
		}

		job, err := controller.Store.GetUploadJob(r.Context(), id)
		if err != nil {
			http.Error(w, "internal error", http.StatusInternalServerError)
			return
		}
		if job == nil {
			http.Error(w, "job not found", http.StatusNotFound)
			return
		}

		resp := jobStatusResponse{
			JobID:          job.ID,
			VideoID:        job.VideoID,
			Status:         job.Status,
			Title:          job.Title,
			BilibiliBvid:   job.BilibiliBvid,
			DownloadFiles:  job.DownloadFiles,
			SubtitleStatus: job.SubtitleStatus,
			ErrorMessage:   job.ErrorMessage,
			CreatedAt:      job.CreatedAt.Format(time.RFC3339),
			UpdatedAt:      job.UpdatedAt.Format(time.RFC3339),
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	})

	mux.HandleFunc("/upload/jobs", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		limit := 50
		if l := r.URL.Query().Get("limit"); l != "" {
			if parsed, err := strconv.Atoi(l); err == nil && parsed > 0 {
				limit = parsed
			}
		}

		jobs, err := controller.Store.ListRecentUploadJobs(r.Context(), limit)
		if err != nil {
			http.Error(w, "internal error", http.StatusInternalServerError)
			return
		}

		var resp []jobStatusResponse
		for _, job := range jobs {
			resp = append(resp, jobStatusResponse{
				JobID:          job.ID,
				VideoID:        job.VideoID,
				Status:         job.Status,
				Title:          job.Title,
				BilibiliBvid:   job.BilibiliBvid,
				DownloadFiles:  job.DownloadFiles,
				SubtitleStatus: job.SubtitleStatus,
				ErrorMessage:   job.ErrorMessage,
				CreatedAt:      job.CreatedAt.Format(time.RFC3339),
				UpdatedAt:      job.UpdatedAt.Format(time.RFC3339),
			})
		}
		if resp == nil {
			resp = []jobStatusResponse{}
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	})

	// List completed jobs that still need subtitle processing.
	mux.HandleFunc("/upload/needs-subtitles", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		limit := 50
		if l := r.URL.Query().Get("limit"); l != "" {
			if parsed, err := strconv.Atoi(l); err == nil && parsed > 0 {
				limit = parsed
			}
		}

		jobs, err := controller.Store.ListJobsNeedingSubtitles(r.Context(), limit)
		if err != nil {
			http.Error(w, "internal error", http.StatusInternalServerError)
			return
		}

		var resp []jobStatusResponse
		for _, job := range jobs {
			resp = append(resp, jobStatusResponse{
				JobID:          job.ID,
				VideoID:        job.VideoID,
				Status:         job.Status,
				Title:          job.Title,
				BilibiliBvid:   job.BilibiliBvid,
				DownloadFiles:  job.DownloadFiles,
				SubtitleStatus: job.SubtitleStatus,
				ErrorMessage:   job.ErrorMessage,
				CreatedAt:      job.CreatedAt.Format(time.RFC3339),
				UpdatedAt:      job.UpdatedAt.Format(time.RFC3339),
			})
		}
		if resp == nil {
			resp = []jobStatusResponse{}
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(resp)
	})

	// Submit subtitle SRT content for a job. Go converts to BCC and uploads to Bilibili.
	mux.HandleFunc("/upload/subtitle", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		var req struct {
			JobID      int64  `json:"job_id"`
			SRTContent string `json:"srt_content"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, "invalid JSON body", http.StatusBadRequest)
			return
		}
		if req.JobID == 0 || req.SRTContent == "" {
			http.Error(w, "job_id and srt_content are required", http.StatusBadRequest)
			return
		}

		// Look up the job to get bvid
		job, err := controller.Store.GetUploadJob(r.Context(), req.JobID)
		if err != nil {
			http.Error(w, "internal error", http.StatusInternalServerError)
			return
		}
		if job == nil {
			http.Error(w, "job not found", http.StatusNotFound)
			return
		}
		if job.BilibiliBvid == "" {
			http.Error(w, "job has no bilibili_bvid yet", http.StatusBadRequest)
			return
		}

		// Get cookie path from uploader
		cookiePath := ""
		if bu, ok := controller.Uploader.(*BiliupUploader); ok {
			cookiePath = bu.opts.CookiePath
		}
		if cookiePath == "" {
			cookiePath = "cookies.json"
		}

		// Mark as generating
		_ = controller.Store.UpdateSubtitleStatus(r.Context(), req.JobID, "generating")

		// Upload to Bilibili
		if err := uploadSubtitleToBilibili(job.BilibiliBvid, req.SRTContent, cookiePath); err != nil {
			log.Printf("subtitle upload failed for job %d: %v", req.JobID, err)
			_ = controller.Store.UpdateSubtitleStatus(r.Context(), req.JobID, "failed")
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]string{
				"status": "failed",
				"error":  err.Error(),
			})
			return
		}

		_ = controller.Store.UpdateSubtitleStatus(r.Context(), req.JobID, "completed")
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
	})

	log.Printf("controller listening on %s", addr)
	return http.ListenAndServe(addr, mux)
}
