package app

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"strconv"
	"time"
)

type uploadRequest struct {
	VideoID      string `json:"video_id"`
	Title        string `json:"title"`
	Description  string `json:"description"`
	Tags         string `json:"tags"`
	PersonaID    string `json:"persona_id"`
	StrategyName string `json:"strategy_name"`
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
	PersonaID      string `json:"persona_id,omitempty"`
	StrategyName   string `json:"strategy_name,omitempty"`
	CreatedAt      string `json:"created_at"`
	UpdatedAt      string `json:"updated_at"`
}

func ServeHTTP(addr string, controller *Controller, queue *JobQueue) error {
	mux := http.NewServeMux()

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

		// Dedup: check uploads table
		uploaded, err := controller.Store.IsUploaded(r.Context(), req.VideoID)
		if err != nil {
			slog.Error("dedup check failed", "table", "uploads", "error", err)
		} else if uploaded {
			resp := uploadResponse{Status: "duplicate", Error: "video already uploaded"}
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusConflict)
			json.NewEncoder(w).Encode(resp)
			return
		}

		// Dedup: check upload_jobs table for active (non-failed) job
		activeJob, err := controller.Store.FindActiveUploadJob(r.Context(), req.VideoID)
		if err != nil {
			slog.Error("dedup check failed", "table", "upload_jobs", "error", err)
		} else if activeJob != nil {
			resp := uploadResponse{JobID: activeJob.ID, Status: "duplicate", Error: "video already has an active job"}
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusConflict)
			json.NewEncoder(w).Encode(resp)
			return
		}

		// Create job in DB
		jobID, err := controller.Store.CreateUploadJob(r.Context(), req.VideoID, req.Title, req.Description, req.Tags, req.PersonaID, req.StrategyName)
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
			slog.Error("failed to write upload response", "error", err)
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
			PersonaID:      job.PersonaID,
			StrategyName:   job.StrategyName,
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
				PersonaID:      job.PersonaID,
				StrategyName:   job.StrategyName,
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

	mux.HandleFunc("/upload/retry-subtitle", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
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
		if err != nil || job == nil {
			http.Error(w, "job not found", http.StatusNotFound)
			return
		}
		if job.Status != "completed" {
			http.Error(w, "job not completed yet", http.StatusBadRequest)
			return
		}
		if job.BilibiliBvid == "" {
			http.Error(w, "job has no bilibili bvid", http.StatusBadRequest)
			return
		}

		// Parse download files
		var files []string
		if err := json.Unmarshal([]byte(job.DownloadFiles), &files); err != nil || len(files) == 0 {
			http.Error(w, "no download files", http.StatusBadRequest)
			return
		}

		// Get subtitle config from queue
		subtitleCfg := queue.GetSubtitleConfig()
		if subtitleCfg == nil {
			http.Error(w, "subtitle pipeline not configured", http.StatusServiceUnavailable)
			return
		}

		// Run in background — generates draft for review, does NOT upload
		go func() {
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Minute)
			defer cancel()
			controller.Store.UpdateSubtitleStatus(ctx, job.ID, "generating")
			if err := RunSubtitlePipeline(ctx, *subtitleCfg, controller.Store, job.ID, files[0], job.BilibiliBvid); err != nil {
				slog.Error("retry-subtitle failed", "job_id", job.ID, "error", err)
				controller.Store.UpdateSubtitleStatus(ctx, job.ID, "failed")
				controller.Store.UpdateUploadJobStatus(ctx, job.ID, job.Status, job.BilibiliBvid, err.Error())
			}
		}()

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusAccepted)
		json.NewEncoder(w).Encode(map[string]any{
			"status": "generating",
			"job_id": job.ID,
			"bvid":   job.BilibiliBvid,
		})
	})

	mux.HandleFunc("/upload/uploaded-ids", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}

		ids, err := controller.Store.GetAllUploadedVideoIDs(r.Context())
		if err != nil {
			http.Error(w, "internal error", http.StatusInternalServerError)
			return
		}
		if ids == nil {
			ids = []string{}
		}

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(ids)
	})

	// GET /upload/subtitle-preview?id=N — preview subtitle draft before publishing
	mux.HandleFunc("/upload/subtitle-preview", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		id, err := strconv.ParseInt(r.URL.Query().Get("id"), 10, 64)
		if err != nil {
			http.Error(w, "invalid id", http.StatusBadRequest)
			return
		}

		draftJSON, err := controller.Store.GetSubtitleDraft(r.Context(), id)
		if err != nil {
			http.Error(w, err.Error(), http.StatusNotFound)
			return
		}

		// Return the raw draft JSON (contains english_srt, chinese_srt, annotations)
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(draftJSON))
	})

	// POST /upload/subtitle-approve?id=N — approve and publish subtitle + danmaku
	mux.HandleFunc("/upload/subtitle-approve", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		id, err := strconv.ParseInt(r.URL.Query().Get("id"), 10, 64)
		if err != nil {
			http.Error(w, "invalid id", http.StatusBadRequest)
			return
		}

		job, err := controller.Store.GetUploadJob(r.Context(), id)
		if err != nil || job == nil {
			http.Error(w, "job not found", http.StatusNotFound)
			return
		}
		if job.Status != "completed" {
			http.Error(w, "job not completed yet", http.StatusBadRequest)
			return
		}
		if job.BilibiliBvid == "" {
			http.Error(w, "job has no bilibili bvid", http.StatusBadRequest)
			return
		}

		subtitleCfg := queue.GetSubtitleConfig()
		if subtitleCfg == nil {
			http.Error(w, "subtitle pipeline not configured", http.StatusServiceUnavailable)
			return
		}

		go func() {
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
			defer cancel()
			if err := ApproveSubtitle(ctx, *subtitleCfg, controller.Store, job.ID, job.BilibiliBvid); err != nil {
				slog.Error("subtitle-approve failed", "job_id", job.ID, "error", err)
				controller.Store.UpdateSubtitleStatus(ctx, job.ID, "failed")
			}
		}()

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusAccepted)
		json.NewEncoder(w).Encode(map[string]any{
			"status": "approving",
			"job_id": job.ID,
			"bvid":   job.BilibiliBvid,
		})
	})

	// POST /upload/annotate?id=N — generate annotations for an existing subtitle draft
	mux.HandleFunc("/upload/annotate", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		id, err := strconv.ParseInt(r.URL.Query().Get("id"), 10, 64)
		if err != nil {
			http.Error(w, "invalid id", http.StatusBadRequest)
			return
		}

		subtitleCfg := queue.GetSubtitleConfig()
		if subtitleCfg == nil || subtitleCfg.MLServiceDir == "" {
			http.Error(w, "annotation not configured (start Go with --ml-service-dir)", http.StatusBadRequest)
			return
		}

		// Load existing draft
		draftJSON, err := controller.Store.GetSubtitleDraft(r.Context(), id)
		if err != nil {
			http.Error(w, "no subtitle draft: "+err.Error(), http.StatusNotFound)
			return
		}

		job, err := controller.Store.GetUploadJob(r.Context(), id)
		if err != nil || job == nil {
			http.Error(w, "job not found", http.StatusNotFound)
			return
		}

		go func() {
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
			defer cancel()
			_ = ctx // timeout handled by the subprocess

			var draft SubtitleDraft
			if err := json.Unmarshal([]byte(draftJSON), &draft); err != nil {
				slog.Error("annotate: failed to parse draft", "job_id", id, "error", err)
				return
			}

			entries, err := fetchAnnotations(*subtitleCfg, draft.ChineseSRT, job.Title)
			if err != nil {
				slog.Error("annotate: annotation failed", "job_id", id, "error", err)
				return
			}
			slog.Info("annotate: generated annotations", "job_id", id, "count", len(entries))

			draft.Annotations = entries
			updatedJSON, _ := json.Marshal(draft)
			if err := controller.Store.SaveSubtitleDraft(ctx, id, string(updatedJSON)); err != nil {
				slog.Error("annotate: failed to save draft", "job_id", id, "error", err)
				return
			}
			_ = controller.Store.UpdateSubtitleStatus(ctx, id, "review")
		}()

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusAccepted)
		json.NewEncoder(w).Encode(map[string]any{
			"status": "annotating",
			"job_id": id,
		})
	})

	// DELETE /upload/job?id=N — remove an upload job from the database
	mux.HandleFunc("/upload/job", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodDelete {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		id, err := strconv.ParseInt(r.URL.Query().Get("id"), 10, 64)
		if err != nil {
			http.Error(w, "invalid id", http.StatusBadRequest)
			return
		}
		if err := controller.Store.DeleteUploadJob(r.Context(), id); err != nil {
			http.Error(w, "delete failed: "+err.Error(), http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{"deleted": id})
	})

	slog.Info("controller listening", "addr", addr)
	return http.ListenAndServe(addr, LoggingMiddleware(mux))
}
