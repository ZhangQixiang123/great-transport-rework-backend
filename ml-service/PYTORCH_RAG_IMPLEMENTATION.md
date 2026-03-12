# PyTorch Embedding Fine-Tuning + RAG Implementation Report

## What Was Built

A two-stage ML pipeline that improves video view prediction for unseen Bilibili channels:

1. **Stage 1 — PyTorch embedding fine-tuning**: A `TitleEmbedder` (nn.Module) learns to project multilingual video titles into 128d vectors optimized for view-count prediction, using a frozen `paraphrase-multilingual-MiniLM-L12-v2` backbone + a trainable projection head.

2. **Stage 2 — RAG vector store**: A numpy-based `VectorStore` indexes all fine-tuned title embeddings and, at inference time, retrieves the top-20 most similar videos by cosine similarity, returning 5 aggregate statistics about their view counts as new features.

The existing LightGBM ranker remains the final predictor — it now trains on 48 features (up from 43), with the 5 new RAG features providing "what happened to similar videos?" context.

## Architecture

```
Training pipeline (two steps):
  Step 1: fine-tune-embeddings
    SQLite DB -> VideoTitleDataset -> DataLoader -> TitleEmbedder + RegressionHead
    -> loss.backward() -> optimizer.step() -> save embedder.pt + vector_store.npz

  Step 2: train (existing, now enhanced)
    Load embedder.pt -> re-encode all titles -> PCA to 20d (replaces old embeddings)
    Load vector_store.npz -> compute 5 RAG features per video
    -> LightGBM trains on 48 features (was 43)

Inference:
    New title -> embedder.encode() -> 128d embedding
                                  |-> PCA to 20d -> title_emb_* features
                                  |-> VectorStore.query() -> 5 RAG features
    48 features -> LightGBM.predict() -> predicted log(views)
```

## New Files

| File | Purpose | PyTorch Concepts |
|------|---------|-----------------|
| `app/embeddings/__init__.py` | Package exports | — |
| `app/embeddings/dataset.py` | `VideoTitleDataset(Dataset)` + `create_dataloaders()` | `Dataset`, `__getitem__`, `DataLoader`, `Subset` |
| `app/embeddings/model.py` | `TitleEmbedder(nn.Module)` with backbone + projection | `nn.Module`, `forward()`, `nn.Linear`, `nn.Sequential`, `requires_grad`, `state_dict`, `torch.no_grad` |
| `app/embeddings/vector_store.py` | `VectorStore` — numpy cosine similarity retrieval | Pure numpy (no PyTorch) |
| `app/embeddings/trainer.py` | `RegressionHead` + `fine_tune_embeddings()` training loop | `backward()`, `optimizer.step()`, `zero_grad()`, `train()/eval()`, `ReduceLROnPlateau`, early stopping |

## Modified Files

| File | Changes |
|------|---------|
| `app/training/features.py` | Added `RAG_FEATURES` (5 names), `FEATURE_NAMES` 43->48, `rag_features` param on extract functions |
| `app/training/trainer.py` | Loads embedder.pt, rebuilds PCA from fine-tuned embeddings, computes RAG features in CV + final training |
| `app/models/ranker.py` | Auto-loads embedder + VectorStore at init, auto-embeds titles at inference, queries RAG features |
| `app/cli.py` | Added `fine-tune-embeddings` subcommand |
| `requirements.txt` | Added `torch>=2.0.0`, `transformers>=4.30.0` |

## The 5 RAG Features

When predicting for a new video, the VectorStore finds the 20 most similar titles (by cosine similarity of fine-tuned embeddings) and computes:

| Feature | Description |
|---------|-------------|
| `rag_similar_median_log_views` | Median log(views) of top-20 similar videos |
| `rag_similar_mean_log_views` | Mean log(views) of top-20 similar videos |
| `rag_similar_std_log_views` | Std dev of log(views) of top-20 similar videos |
| `rag_similar_max_log_views` | Max log(views) of top-20 similar videos |
| `rag_top5_mean_log_views` | Mean log(views) of top-5 most similar videos |

During cross-validation, videos from the test fold's channels are excluded from retrieval to prevent data leakage.

## Data Leakage Prevention

- **CV RAG queries**: `exclude_channel` removes all videos from test-fold channels, so the model can't peek at its own channel's performance
- **Self-exclusion**: `exclude_bvid` always removes the query video itself from results
- **No circular features**: views/likes/coins are never used as inputs (unchanged from before)

## Test Coverage

| Test File | Tests | What's Covered |
|-----------|-------|---------------|
| `test_embeddings_dataset.py` | 7 | Dataset length, getitem shapes/keys/values, DataLoader batching |
| `test_embeddings_model.py` | 9 | Forward shape, projection dim, frozen/unfrozen grads, encode(), save/load roundtrip |
| `test_vector_store.py` | 14 | Build, query, exclude_bvid, exclude_channel, empty store, save/load, feature correctness |
| `test_embeddings_trainer.py` | 5 | Training completes, early stopping triggers, insufficient data returns None, RegressionHead shape |
| `test_features.py` | 50 (was 46) | +4 new: RAG defaults to 0, RAG pass-through, partial defaults, dataframe integration |

**Total: 85 tests across these files, all passing.**

## Backward Compatibility

- All RAG features default to 0.0 when no embedder/VectorStore is present
- `extract_features_single()` and `extract_features_dataframe()` new params are optional with default `None`
- `RankerModel` gracefully falls back if `embedder.pt` or `vector_store.npz` don't exist
- Old models trained on 43 features continue to work (metadata stores feature_names)
- The `train` command works identically if no `embedder.pt` exists in model_dir

---

## How to Train

### Prerequisites

```bash
cd ml-service
# Activate venv
.venv\Scripts\activate   # Windows
# or: source .venv/bin/activate  # Linux/Mac

# Install new dependencies (one-time)
pip install torch>=2.0.0 transformers>=4.30.0
```

### Step 1: Fine-tune the title embedder

This trains the PyTorch embedding model and builds the RAG vector store. Run this first:

```bash
python -m app.cli --db-path data.db fine-tune-embeddings --model-dir models
```

**What happens:**
1. Loads all videos with views > 0 from the database (~7,791 videos)
2. Splits by channel (GroupKFold) for validation
3. Trains TitleEmbedder projection head (backbone frozen by default) with MSE loss on log(views)
4. Early stops when validation loss plateaus (default patience=5)
5. Saves `models/embedder.pt` (the fine-tuned embedder)
6. Encodes all video titles and saves `models/vector_store.npz`

**Options:**
```
--epochs 30          # Max training epochs (default: 30)
--batch-size 64      # Batch size (default: 64)
--learning-rate 1e-3 # Learning rate (default: 1e-3)
--projection-dim 128 # Embedding dimension (default: 128)
--patience 5         # Early stopping patience (default: 5)
--full-finetune      # Unfreeze transformer backbone (slower, may overfit)
```

**Expected output:**
```
Fine-tuning complete!
  Best epoch: 12/30
  Best val loss: 2.3456
  Videos: 7791, Channels: 31
  Projection dim: 128
  Vector store: 7791 entries
  Device: cuda
```

**First run note:** The first run will download `paraphrase-multilingual-MiniLM-L12-v2` (~470MB) from HuggingFace. Subsequent runs use the cached model.

### Step 2: Train the LightGBM ranker

After fine-tuning, retrain the LightGBM model. It will automatically detect and use the embedder:

```bash
python -m app.cli --db-path data.db train --model-dir models
```

**What happens (enhanced with embedder):**
1. Detects `models/embedder.pt` — re-encodes all titles with fine-tuned embedder
2. Runs PCA on 128d fine-tuned embeddings -> 20d (replaces old static embeddings)
3. Loads `models/vector_store.npz` and computes 5 RAG features per video
4. During 5-fold GroupKFold CV: excludes test-fold channels from RAG retrieval
5. Trains final LightGBM on all 48 features
6. Saves `models/latest_model.json` + `models/latest_model_meta.json`

**If no embedder exists**, the train command works exactly as before (43 features, no RAG).

### Step 3: Verify

```bash
# Run all tests
python -m pytest tests/ -x --ignore=tests/test_bilibili_tracker.py

# Check CV metrics in the training output — look for:
#   CV Average: RMSE=..., R2=..., Correlation=...
# Target: correlation > 0.35 (up from 0.27 baseline)
```

### Full pipeline in one go

```bash
# 1. Fine-tune embeddings (PyTorch)
python -m app.cli --db-path data.db fine-tune-embeddings --model-dir models --epochs 30

# 2. Train LightGBM (now with RAG features)
python -m app.cli --db-path data.db train --model-dir models

# 3. (Optional) Run discovery with enhanced model
python -m app.cli --db-path data.db discover --model-dir models
```

### Artifacts produced

After both steps, `models/` will contain:

| File | Size (approx) | Contents |
|------|---------------|----------|
| `embedder.pt` | ~180MB | TitleEmbedder state_dict + config |
| `vector_store.npz` | ~4MB | 7791 x 128 embeddings + metadata |
| `latest_model.json` | ~2MB | LightGBM booster |
| `latest_model_meta.json` | ~50KB | Feature names, thresholds, CV metrics |

### GPU vs CPU

- The fine-tuning step auto-detects CUDA and uses GPU if available
- With a frozen backbone (default), training takes ~2-5 minutes on GPU, ~10-15 minutes on CPU
- With `--full-finetune` (unfrozen backbone), expect 5-10x longer training times
- The LightGBM training step is CPU-only (unchanged)
