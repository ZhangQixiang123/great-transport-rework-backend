# ML Training Pipeline — Implementation Plan

**Hardware:** i9 CPU + RTX 4080 Laptop GPU
**Strategy:** LightGBM with `device=gpu` for GPU-accelerated training; parallel feature extraction via pandas vectorized ops on i9

---

## Progress Tracker

### Step 1: Update `requirements.txt`
- [x] Add numpy, pandas, scikit-learn, lightgbm, joblib

### Step 2: Create `app/training/features.py`
- [x] 22 tabular features (raw metrics, ratios, content, time, derived)
- [x] `extract_features_single()`, `extract_features_dataframe()`, `extract_labels()`
- [x] `load_training_data(db)` entry point
- [x] Label mapping: failed=0, standard=1, successful=2, viral=3

### Step 3: Create `app/training/data_validator.py`
- [x] Min 50 samples (error), min 5 per class (error), min 2 classes (error)
- [x] Warnings if <200 total or <20 per class
- [x] `ValidationResult` dataclass

### Step 4: Create `app/training/evaluator.py`
- [x] Accuracy, weighted F1, macro F1, log loss
- [x] Per-class precision/recall/F1, one-vs-rest AUC
- [x] Confusion matrix, feature importance (gain)
- [x] `EvaluationReport` with `summary()` and `to_dict()`

### Step 5: Create `app/training/trainer.py`
- [x] LightGBM training with GPU support (`device=gpu`, `gpu_platform_id=0`, `gpu_device_id=0`)
- [x] Auto-detect GPU availability, fallback to CPU
- [x] Stratified 80/20 split, early stopping (50 rounds), `is_unbalance=True`
- [x] Save model `.txt` + metadata `.json` to `models/`
- [x] Copy as `latest_model.txt` / `latest_model_meta.json`

### Step 6: Create `app/models/ranker.py`
- [x] `RankerModel(model_path)` load from file
- [x] `load_latest(model_dir)` class method
- [x] `predict_proba()`, `predict_label()`, `predict_video()`

### Step 7: Add `get_labeled_competitor_videos()` to `database.py`
- [x] WHERE label IS NOT NULL AND label != '', ORDER BY publish_time ASC

### Step 8: Add `train` command to `cli.py`
- [x] Subparser: `--model-dir`, `--test-size`, `--num-rounds`, `--learning-rate`, `--min-samples`, `--gpu/--no-gpu`
- [x] `cmd_train()` handler (sync, CPU/GPU-bound)
- [x] Dispatch in `main()` + output formatting

### Step 9: Create `app/training/__init__.py` and `app/models/__init__.py`
- [x] Package init files with exports

### Step 10: Create `models/.gitkeep`
- [x] Empty file for git tracking

### Step 11: Write tests
- [x] `test_features.py` — duration buckets, extraction, zero-views, labels
- [x] `test_data_validator.py` — valid/invalid/edge cases
- [x] `test_trainer.py` — synthetic training, file persistence, insufficient data
- [x] `test_evaluator.py` — report shape, metric ranges, JSON serializable
- [x] `test_ranker.py` — save/load round-trip, predictions, file-not-found

### Step 12: Run tests & verify
- [ ] `pip install -r requirements.txt`
- [ ] `pytest tests/ -v` — all pass
- [ ] `python -m app.cli --db-path test.sqlite train` — graceful no-data error

---

## GPU Utilization Notes

**LightGBM GPU mode** uses OpenCL on the RTX 4080:
- `device: "gpu"` in params enables it
- `gpu_platform_id: 0`, `gpu_device_id: 0` selects the 4080
- Most impactful with large datasets (>10k rows); for small data CPU may be faster
- The trainer auto-detects GPU and falls back to CPU if unavailable

**i9 CPU** is leveraged via:
- Pandas vectorized feature extraction (no Python loops over rows)
- scikit-learn parallelized metrics (`n_jobs=-1` where applicable)
- LightGBM `num_threads` defaults to all cores on CPU fallback

**Future GPU use:**
- DistilBERT/CLIP embeddings would run on CUDA via PyTorch on the 4080
- That's the real GPU payoff — documented for later implementation
