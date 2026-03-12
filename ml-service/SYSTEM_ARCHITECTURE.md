# ML & LLM System Architecture

## Overview

This system predicts how well a YouTube video will perform if re-uploaded ("transported") to Bilibili, then automates the full discovery-to-upload pipeline. It combines three AI subsystems:

1. **ML Prediction** — LightGBM regression on 48 features to predict Bilibili view counts
2. **Fine-tuned Embeddings** — Multilingual sentence embeddings trained to encode "viral potential" into vector space
3. **LLM Discovery** — Ollama-hosted Qwen 2.5 7B for keyword translation, title translation, and relevance scoring

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Daily Job Orchestrator                       │
│                         (daily_job.py)                              │
├───────────┬───────────────────┬──────────────────┬──────────────────┤
│ Discovery │  Video Selection  │  Title Translate  │  Download/Upload │
│ Pipeline  │  (top-N by score) │  (LLM zh->en)    │  (yt-transfer)   │
├───────────┴───────────────────┴──────────────────┴──────────────────┤
│                                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────────┐  │
│  │ LLM Scoring  │  │ ML Ranker    │  │ Fine-tuned Embeddings     │  │
│  │ (Qwen 2.5)   │  │ (LightGBM)  │  │ (MiniLM + Projection)    │  │
│  │              │  │              │  │                           │  │
│  │ - translate  │  │ - 48 features│  │ - 128d title vectors      │  │
│  │ - relevance  │  │ - log(views) │  │ - PCA -> 20d features     │  │
│  │   scoring    │  │   prediction │  │ - VectorStore RAG         │  │
│  └──────────────┘  └──────────────┘  └───────────────────────────┘  │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    SQLite Database                            │   │
│  │  competitor_channels | competitor_videos | youtube_stats      │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 1. ML Prediction Pipeline

### 1.1 Model

**Algorithm:** LightGBM gradient-boosted decision tree regression.

Two modes are available:
- **Pure LightGBM** (`--no-random-intercepts`): Standard GBDT. Preferred for production — generalizes better to unseen channels.
- **GPBoost mixed-effects** (`use_random_intercepts=True`): LightGBM fixed effects + per-channel random intercepts. Better for known channels, but overfits on new ones.

**Target variable:** `log1p(bilibili_views)` — log-transformed Bilibili view count. This stabilizes variance across orders of magnitude (videos range from hundreds to millions of views).

**Hyperparameters:**

| Parameter | Value |
|-----------|-------|
| Objective | `regression_l2` (MSE) |
| Learning rate | 0.05 |
| Num leaves | 63 |
| Feature fraction | 0.8 |
| Min data in leaf | 20 |
| Num boost rounds | 500 (default) |

**Cross-validation:** `GroupKFold` with 5 folds, grouped by channel. This ensures no channel appears in both train and test sets, simulating the real scenario of predicting for channels not in the training data.

**Classification output:** The continuous log(views) prediction is mapped to four buckets using percentile thresholds computed from training predictions:
- **Failed** — below p25
- **Standard** — p25 to p75
- **Successful** — p75 to p95
- **Viral** — above p95

### 1.2 Features (48 total)

#### Pre-upload Features (10)

Available before any video is uploaded. These describe the video's metadata.

| Feature | Description | Type |
|---------|-------------|------|
| `duration` | Video duration in seconds | Numeric |
| `duration_bucket` | 0=<3m, 1=3-10m, 2=10-30m, 3=>30m | Categorical |
| `title_length` | Character count of the title | Numeric |
| `title_has_number` | Whether the title contains digits | Binary |
| `description_length` | Character count of the description | Numeric |
| `publish_hour_sin` | sin(2π × hour / 24) | Numeric [-1, 1] |
| `publish_hour_cos` | cos(2π × hour / 24) | Numeric [-1, 1] |
| `publish_dow_sin` | sin(2π × day_of_week / 7) | Numeric [-1, 1] |
| `publish_dow_cos` | cos(2π × day_of_week / 7) | Numeric [-1, 1] |
| `has_youtube_source` | Whether a YouTube source ID exists | Binary |

Publish time uses cyclical encoding (sin/cos) so that the model understands that hour 23 and hour 0 are adjacent, not 23 apart.

#### Clickbait Features (3)

Surface-level indicators of title engagement style.

| Feature | Description |
|---------|-------------|
| `title_exclamation_count` | Count of `!` and `！` (fullwidth) |
| `title_question_count` | Count of `?` and `？` (fullwidth) |
| `title_caps_ratio` | Ratio of uppercase letters to total letters |

#### YouTube Original Stats (7)

Performance metrics from the original YouTube video. Only used when the video was matched by `source_id` (reliable link); title-search matches are excluded as noisy.

| Feature | Description |
|---------|-------------|
| `yt_log_views` | log1p(youtube_views) |
| `yt_log_likes` | log1p(youtube_likes) |
| `yt_log_comments` | log1p(youtube_comments) |
| `yt_duration_seconds` | YouTube-reported duration |
| `yt_like_view_ratio` | likes / views |
| `yt_comment_view_ratio` | comments / views |
| `yt_category_id` | YouTube category (numeric) |

**Important design choice:** Bilibili views/likes/coins are NOT used as input features — that would be circular since Bilibili views is the target. Only YouTube-side stats are used.

#### Additional Features (3)

| Feature | Description |
|---------|-------------|
| `yt_tag_count` | Number of tags on the YouTube video |
| `yt_upload_delay_days` | Days between YouTube publish and Bilibili publish (≥0) |
| `yt_stats_imputed` | Binary flag: 1 if YT stats were imputed, 0 if real |

#### Title Embedding Features (20)

Dense semantic representation of the video title. Derived from the fine-tuned embedder (see Section 2).

| Feature | Description |
|---------|-------------|
| `title_emb_0` through `title_emb_19` | PCA-reduced embedding dimensions |

The raw 128-dimensional embedding from the fine-tuned model is reduced to 20 dimensions via PCA, which captures 96.9% of variance (vs 46.6% with static embeddings).

#### RAG Features (5)

Retrieval-augmented features based on the most similar historical videos (see Section 3).

| Feature | Description |
|---------|-------------|
| `rag_similar_median_log_views` | Median log(views) of top-20 similar videos |
| `rag_similar_mean_log_views` | Mean log(views) of top-20 similar videos |
| `rag_similar_std_log_views` | Std dev of log(views) of top-20 similar videos |
| `rag_similar_max_log_views` | Max log(views) of top-20 similar videos |
| `rag_top5_mean_log_views` | Mean log(views) of top-5 most similar videos |

These features answer: "How did historically similar videos perform?" This is the system's most powerful signal for cold-start prediction.

### 1.3 Imputation

When YouTube stats are unavailable (no `source_id` match), the system imputes from:
1. **Per-channel averages** — mean of each YT stat across the channel's other videos
2. **Global averages** — mean across all videos (fallback if channel has no stats)

The `yt_stats_imputed` flag tells the model which videos have real vs imputed stats, allowing it to weight them accordingly. During cross-validation, imputation stats are computed from training folds only.

---

## 2. Fine-tuned Embedding System

### 2.1 Architecture

```
              TitleEmbedder
┌────────────────────────────────────────┐
│                                        │
│   Input: Video title (Chinese/English) │
│              │                         │
│   ┌──────────▼──────────┐              │
│   │  MiniLM-L12-v2      │  384-dim     │
│   │  (frozen backbone)  │  output      │
│   └──────────┬──────────┘              │
│              │                         │
│   ┌──────────▼──────────┐              │
│   │  Projection Head    │              │
│   │  Linear(384→128)    │              │
│   │  ReLU               │              │
│   │  Dropout(0.1)       │              │
│   │  Linear(128→128)    │              │
│   └──────────┬──────────┘              │
│              │                         │
│   Output: 128-dim embedding            │
└────────────────────────────────────────┘
```

**Backbone:** `paraphrase-multilingual-MiniLM-L12-v2` — a 12-layer multilingual sentence transformer supporting 50+ languages. Critical for this project since titles are a mix of Chinese and English.

**Pooling:** Mean pooling over non-padding tokens from the last hidden state.

**Projection head:** Two-layer MLP (384→128→128) with ReLU and dropout. This transforms generic sentence similarity into a space where proximity correlates with view count similarity.

**Freeze strategy:** By default, only the projection head trains (~66K parameters). Full fine-tuning is available via `--full-finetune` with differential learning rates:
- Projection head: 1e-3
- Backbone: 2e-5 (50x smaller to prevent catastrophic forgetting)

### 2.2 Training Objective

The embedder is trained with an auxiliary regression head that is **discarded after training**:

```
  128-dim embedding
        │
┌───────▼───────────┐
│  Regression Head   │  (discarded after training)
│  Linear(128→64)    │
│  ReLU              │
│  Dropout(0.1)      │
│  Linear(64→1)      │
└───────┬────────────┘
        │
  Predicted log(views)
```

**Loss:** MSE between predicted and actual `log1p(views)`. This forces the 128-dim embedding to encode information predictive of view count, not just generic semantic similarity.

**Training details:**
- Optimizer: Adam
- Scheduler: ReduceLROnPlateau (factor=0.5, patience=2)
- Early stopping: patience=5, restores best checkpoint
- Validation: GroupKFold split by channel (3 folds, uses first split)
- Mixed precision (fp16) on CUDA

### 2.3 Why Fine-tune?

Static sentence embeddings encode generic semantic similarity. Fine-tuning reshapes the embedding space so that vectors with similar view counts cluster together. This yields:
- **PCA variance retention:** 96.9% in 20 dims (vs 46.6% static) — the embedding dimensions are more aligned and less noisy
- **Better ML features:** The 20 PCA-reduced dimensions carry more predictive signal per dimension

---

## 3. RAG (Retrieval-Augmented Generation) System

### 3.1 Vector Store

A lightweight numpy-based vector store (no external dependencies like FAISS or Pinecone).

**Storage format:** `.npz` file containing:
- `embeddings` — [N, 128] float32 matrix of fine-tuned title embeddings
- `norms` — pre-computed L2 norms for fast cosine similarity
- `bvids` — Bilibili video IDs
- `log_views` — log1p(views) for each video
- `channel_ids` — channel UIDs

### 3.2 Query Flow

```
Input title
    │
    ▼
embedder.encode(title)  →  128-dim query vector
    │
    ▼
cosine_similarity(query, all_embeddings)  →  [N] scores
    │
    ▼
exclude self + (optionally) same-channel videos
    │
    ▼
argpartition top-20 most similar
    │
    ▼
Compute 5 aggregate features from top-20 log(views):
  • median, mean, std, max, top-5 mean
```

**Cosine similarity** is computed as a single matrix multiplication: `similarities = embeddings @ query / (norms × query_norm)`.

**Channel exclusion:** During cross-validation, when computing RAG features for the test fold, all videos from test-fold channels are excluded. This prevents data leakage — the model cannot learn that "videos from channel X get Y views" through the RAG features.

### 3.3 Why RAG Features Help

RAG features provide a **content-based prior**: "Videos with similar titles historically got X views." This is especially valuable for:
- **Cold-start channels** — no channel history available, but similar content exists
- **Trend detection** — if several similar recent videos went viral, the RAG mean/max will be high
- **Topic sensitivity** — some topics consistently underperform or overperform on Bilibili

---

## 4. LLM Discovery System

### 4.1 LLM Configuration

**Model:** Qwen 2.5 7B, served locally via Ollama.

**Structured output:** All LLM calls use Pydantic models with `format="json"` in the Ollama API, enforcing valid JSON output. Responses are validated with `model_validate_json()`.

### 4.2 Three LLM Capabilities

#### Keyword Translation

```
Input:  Chinese Bilibili trending keyword (e.g., "原神新角色")
Output: TranslatedKeyword {
          queries: ["genshin impact new character", "genshin new playable"],
          topic: "Gaming - Genshin Impact character release"
        }
```

Generates 2-3 English YouTube search queries from a Chinese trending keyword. The topic summary helps with relevance scoring downstream.

#### Title Translation

```
Input:  English YouTube title + channel name
Output: TranslatedTitle {
          title: "Chinese translated title suitable for Bilibili"
        }
```

Used at upload time to create a Chinese title for the transported video.

#### Relevance Scoring

```
Input:  Bilibili keyword + YouTube video (title, channel, description, stats)
Output: RelevanceResult {
          score: 0.85,     # 0.0 to 1.0
          reasoning: "...",
          topics: ["gaming", "genshin impact"]
        }
```

Scores how relevant a YouTube video is to a Bilibili trending topic. Videos scoring below 0.1 are filtered out.

### 4.3 System Prompt Design

All three capabilities share a system prompt identifying the LLM as a "cross-platform video content analyst specializing in content that performs well when transported between YouTube and Bilibili." This frames the task as cultural bridge-building rather than generic translation.

---

## 5. How ML and LLM Interact

### 5.1 During Training

ML and LLM are **independent during training**. The training pipeline is purely ML:

```
Database  →  Feature extraction  →  LightGBM training
                  │
                  ├── 10 pre-upload features (computed from metadata)
                  ├── 3 clickbait features (computed from title text)
                  ├── 7 YouTube stats (from DB joins)
                  ├── 3 additional features (computed from metadata)
                  ├── 20 embedding features (from fine-tuned embedder + PCA)
                  └── 5 RAG features (from VectorStore similarity search)
```

The LLM is not involved in training at all. The fine-tuned embedder is a small neural network (not an LLM) that produces the embedding and RAG features consumed by LightGBM.

### 5.2 During Inference (Discovery Pipeline)

This is where ML and LLM **collaborate in a pipeline**:

```
Step 1: LLM translates Bilibili keywords → English search queries
Step 2: YouTube API returns candidate videos
Step 3: LLM scores relevance of each candidate (0-1)
Step 4: ML predicts Bilibili views for each candidate
Step 5: Combined score = 0.2×heat + 0.4×LLM_relevance + 0.4×ML_prediction
Step 6: Rank by combined score, save top candidates
Step 7: LLM translates title of selected videos for Bilibili upload
```

The key insight is that **LLM and ML contribute complementary signals**:

| Signal | Source | What it captures |
|--------|--------|-----------------|
| Heat score | YouTube API | Raw popularity on YouTube |
| Relevance | LLM (Qwen 2.5) | Cultural fit for Bilibili audience |
| Predicted views | ML (LightGBM) | Historical pattern matching from 7,791 training videos |

The LLM understands **why** content might resonate (cultural relevance, topic interest) while the ML model understands **how much** based on statistical patterns (duration sweet spots, engagement ratios, title patterns that historically correlate with views).

### 5.3 Scoring Formula

```python
combined_score = (
    0.20 * heat_score +        # YouTube popularity (log-normalized vs 5M cap)
    0.40 * llm_relevance +     # LLM cultural relevance (0-1)
    0.40 * predicted_views     # ML predicted Bilibili views (log-normalized vs 1M cap)
)
```

If the ML model is unavailable, `predicted_views` defaults to 0.5 (neutral), and the system degrades gracefully to LLM + heat scoring only.

---

## 6. Training Workflow

The full training pipeline has two steps, run sequentially:

### Step 1: Fine-tune Embeddings

```bash
python -m app.cli --db-path data.db fine-tune-embeddings
```

1. Loads all videos with views > 0 from the database
2. Trains TitleEmbedder (frozen MiniLM + projection head) with MSE loss on log(views)
3. Saves `embedder.pt` to models directory
4. Encodes all training titles into 128-dim vectors
5. Builds and saves `vector_store.npz`

### Step 2: Train LightGBM

```bash
python -m app.cli --db-path data.db train --no-random-intercepts
```

1. Loads training data from database
2. Loads `embedder.pt` from Step 1
3. Encodes all titles → 128-dim → PCA(20-dim), saves `pca.pkl`
4. Rebuilds VectorStore for RAG feature computation
5. Runs 5-fold GroupKFold cross-validation (channel-grouped)
6. Trains final model on all data
7. Computes classification thresholds from predictions
8. Saves `latest_model.json` + `latest_model_meta.json`

### Why Two Steps?

The embedder must be trained first because:
- LightGBM needs the 20 embedding features and 5 RAG features as input
- The VectorStore must be populated with fine-tuned embeddings for RAG queries
- PCA must be fitted on the fine-tuned embedding space

---

## 7. Inference Workflow

### Single Video Prediction

```python
ranker = RankerModel.load_latest(model_dir="models", db=db)
result = ranker.predict_video(video, yt_stats=stats)
# result = {"label": "successful", "predicted_log_views": 11.2, "predicted_views": 73130}
```

Internal flow:
1. Resolve YouTube stats (use provided, or impute from channel/global averages)
2. Encode title via `embedder.encode()` → 128-dim vector
3. PCA transform → 20-dim `title_emb_*` features
4. Query VectorStore → 5 RAG features
5. Extract all 48 features
6. LightGBM predict → log(views)
7. Classify via percentile thresholds → label

### Discovery Pipeline Prediction

During discovery, the system creates a dummy `CompetitorVideo` from each YouTube candidate and calls `predict_video()` with the YouTube stats passed directly (no imputation needed since the YouTube API provides real stats).

---

## 8. Data Leakage Prevention

The system implements several safeguards against data leakage:

1. **GroupKFold by channel** — No channel appears in both train and test folds. Prevents learning channel-specific patterns that don't generalize.

2. **RAG channel exclusion** — During CV, RAG queries for test-fold videos exclude all videos from test-fold channels. Prevents the model from inferring "similar videos from channel X got Y views."

3. **Imputation from training fold only** — YouTube stat imputation averages are computed exclusively from training fold data. Prevents leaking test-fold statistics into features.

4. **No Bilibili engagement features** — Views, likes, and coins from Bilibili are never used as input features (they're the target or target-correlated).

5. **YouTube match method filter** — Only `source_id` matches are used for YouTube stats. Title-search matches introduce noise and potential label leakage.

---

## 9. Production Architecture

### Daily Job Orchestration

```
daily_job.py (cron-scheduled)
    │
    ├── 1. Run discovery pipeline (10 min timeout)
    │       ├── Fetch Bilibili trending keywords
    │       ├── LLM translate → English queries
    │       ├── YouTube API search
    │       ├── LLM score relevance
    │       ├── ML predict views
    │       └── Save ranked candidates to DB
    │
    ├── 2. Pick top-N pending videos from DB
    │
    └── 3. For each selected video:
            ├── LLM translate title to Chinese
            ├── Build description with YouTube attribution
            ├── yt-transfer: download + upload (30 min timeout)
            ├── Parse BV ID from output
            └── Upload CC subtitles if available
```

### Model Artifacts

| File | Description |
|------|-------------|
| `models/embedder.pt` | Fine-tuned TitleEmbedder weights |
| `models/vector_store.npz` | Pre-computed embeddings + metadata for RAG |
| `models/pca.pkl` | Fitted PCA(128→20) transform |
| `models/latest_model.json` | LightGBM booster (serialized) |
| `models/latest_model_meta.json` | Metadata: feature names, imputation stats, thresholds |

---

## 10. Current Performance

| Metric | Value |
|--------|-------|
| CV R² | -0.07 |
| CV Pearson Correlation | 0.44 |
| Training data | 7,791 videos from 31 channels |
| With YouTube stats | 4,493 videos |

The negative R² indicates the model's MSE is slightly worse than predicting the mean, but the 0.44 correlation shows meaningful ranking ability — the model reliably distinguishes high-potential from low-potential videos, even if it can't precisely predict exact view counts. For the purpose of ranking candidates in the discovery pipeline, correlation matters more than R².

Previous baseline (43 features, no embeddings/RAG): R²=-0.37, correlation=0.27.
Adding fine-tuned embeddings and RAG improved correlation by 63%.

---

## 11. LLM + Web RAG + Neural Reranker (New Architecture)

The system has been extended with a three-tier prediction fallback chain that augments the existing LightGBM pipeline with LLM-based prediction and a PyTorch neural reranker.

### 11.1 Prediction Fallback Chain

```
Candidate Video
     |
     +-> Web RAG: Search Bilibili + YouTube for similar videos
     |        +-> Similar video stats (views, likes, duration)
     |
     +-> LLM Predictor: Analyze candidate + similar videos
     |        +-> Structured features (cultural_fit, trend_score, etc.)
     |
     +-> Neural Reranker (PyTorch): Combine all signals
              +-- Candidate stats (YT views, likes, duration)
              +-- LLM features (cultural_fit, trend, quality, etc.)
              +-- Similar video set (via attention mechanism)
              -> Predicted log(views)
```

**Fallback order:**
1. **Neural Reranker** (if `models/reranker.pt` exists) — highest quality, uses all signals
2. **LLM Predictor** (structured prediction from LLM) — no training required, uses web RAG context
3. **LightGBM** (existing 48-feature model) — baseline statistical fallback

### 11.2 LLM Backend Abstraction (`app/llm/`)

Unified interface for local and cloud LLMs, replacing the hardcoded Ollama dependency.

- **`LLMBackend`** — Python Protocol defining `chat(messages, json_schema) -> str`
- **`OllamaBackend`** — local Ollama (default: qwen2.5:7b)
- **`CloudBackend`** — OpenAI (`gpt-4o-mini`) or Anthropic (`claude-sonnet-4-5-20250929`) APIs
- **`create_backend(type, model)`** — factory function

The `LLMScorer` (relevance scoring, title translation) and `LLMPredictor` both accept any `LLMBackend`, enabling switching between local and cloud models via CLI flags or environment variables.

### 11.3 Web RAG Module (`app/web_rag/`)

Searches both Bilibili and YouTube for videos similar to the candidate, providing real-world calibration data for the LLM and neural reranker.

| Component | File | Description |
|-----------|------|-------------|
| Bilibili search | `bilibili_search.py` | `search_bilibili_similar()` via `bilibili_api.search.search_by_type()` |
| YouTube search | `youtube_similar.py` | `search_youtube_similar()` via YouTube Data API v3 |
| Aggregator | `aggregator.py` | `WebRAGAggregator.search()` combines results into `WebRAGContext` |

`WebRAGContext` provides:
- `similar_videos` — unified `SimilarVideo` list from both platforms (title, views, likes, duration, platform, rank)
- `aggregate_stats()` — median/mean/std/max/min views
- `format_for_llm()` — text summary injected into the LLM prediction prompt
- `to_reranker_features()` — numeric feature dicts for the neural reranker input

### 11.4 LLM Predictor (`app/prediction/llm_predictor.py`)

Asks the LLM to analyze the candidate video plus web RAG context and produce 8 structured scores:

| Score | Range | Description |
|-------|-------|-------------|
| `estimated_log_views` | 0-16 | log1p(predicted Bilibili views) |
| `confidence` | 0-1 | LLM's confidence in the prediction |
| `cultural_fit` | 0-1 | How well content fits Chinese audience |
| `trend_alignment` | 0-1 | Alignment with current Bilibili trends |
| `content_quality` | 0-1 | Production quality signal |
| `audience_overlap` | 0-1 | YouTube/Bilibili audience overlap |
| `novelty_score` | 0-1 | Uniqueness on Bilibili |
| `transport_suitability` | 0-1 | How well video transports (subtitle-friendly, visual) |

All scores are clamped to their valid ranges after LLM response parsing. The LLM uses similar video performance as calibration anchors in its prompt.

### 11.5 Neural Reranker (`app/prediction/neural_reranker.py`)

A PyTorch `nn.Module` cross-encoder that learns to combine candidate features, LLM features, and similar video sets:

```
NeuralReranker(nn.Module):
  +-- nn.Embedding (category_id, duration_bucket)     -> categorical encoding
  +-- nn.Sequential (candidate encoder)                -> dense layers for numerics + LLM features
  +-- SimilarVideoEncoder (nn.MultiheadAttention)      -> self-attention over similar videos
  +-- nn.MultiheadAttention (cross-attention)          -> candidate attends to similar videos
  +-- nn.TransformerEncoderLayer (refinement)          -> final self-attention
  +-- nn.Sequential (prediction head)                  -> Linear->ReLU->Linear->1
```

**Input features (45 total):**
- 15 candidate numeric: yt_log_views/likes/comments, duration, engagement ratios, cyclical time encoding, title_length, clickbait signals, heat_score, relevance_score
- 8 LLM features: all scores from `LLMPrediction.to_feature_dict()`
- 2 categorical via `nn.Embedding(dim=16)`: yt_category_id (50 categories), duration_bucket (8 buckets)
- Up to 20 similar videos, each with 5 features: log_views, log_likes, duration, platform (bilibili=1/youtube=0), rank_position

**Key architectural decisions:**
- **Self-attention** over similar videos encodes inter-video relationships (e.g., "many similar videos are popular" vs "one viral outlier")
- **Cross-attention** lets the candidate query attend to specific similar videos (e.g., "find the similar video most relevant to this candidate")
- **All-padded handling**: When no similar videos are available, attention outputs are zeroed out and the model relies solely on candidate + LLM features

### 11.6 Dataset & Training (`app/prediction/dataset.py`, `trainer.py`)

**Dataset:**
- `RerankerDataset` — handles mixed input types with variable-length similar video lists
- `reranker_collate_fn` — pads similar videos to `MAX_SIMILAR_VIDEOS=20` with boolean masks

**Training loop:**
- GroupKFold cross-validation by channel (matches existing LightGBM training)
- AdamW optimizer (weight_decay=0.01) + OneCycleLR scheduler
- Gradient clipping (max_norm=1.0)
- Mixed precision (`torch.amp`) on CUDA, standard precision on CPU
- Early stopping with patience=8, restoring best checkpoint weights
- Final model trained on all data and saved to `models/reranker.pt`

**Training data source:** During training, similar videos come from the local VectorStore (simulating web RAG). During inference, they come from live Bilibili + YouTube search.

### 11.7 VectorStore Enhancement

Added `query_detailed()` to `app/embeddings/vector_store.py` — returns per-video data (bvid, log_views, similarity, rank) instead of just aggregate statistics. Used by the reranker trainer to simulate web RAG during training.

### 11.8 CLI Commands

```bash
# Train the neural reranker (after fine-tune-embeddings and train)
python -m app.cli --db-path data.db train-reranker [--epochs 50] [--batch-size 32] [--learning-rate 1e-3]

# Test prediction on a single video
python -m app.cli --db-path data.db predict <youtube_url_or_id> [--backend ollama|openai|anthropic]

# Run discovery with configurable backend
python -m app.cli --db-path data.db discover --backend ollama|openai|anthropic
```

### 11.9 Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_BACKEND` | `ollama` | Backend type for daily_job.py (ollama/openai/anthropic) |
| `OPENAI_API_KEY` | — | Required for OpenAI backend |
| `ANTHROPIC_API_KEY` | — | Required for Anthropic backend |

### 11.10 Model Artifacts (New)

| File | Description |
|------|-------------|
| `models/reranker.pt` | Trained NeuralReranker weights + config |

### 11.11 Test Coverage

68 new tests across 3 files (317 total passing):

| Test file | Tests | Coverage |
|-----------|-------|----------|
| `test_web_rag.py` | 17 | Bilibili/YouTube search, WebRAGContext, aggregator |
| `test_llm_predictor.py` | 18 | Backend protocol, all 3 backends, LLMPredictor, structured output |
| `test_neural_reranker.py` | 33 | Model shapes, gradient flow, save/load, dataset, collate_fn, training convergence |

### 11.12 PyTorch Concepts Covered

| Concept | Location |
|---------|----------|
| `nn.Module`, `forward()` | neural_reranker.py |
| `nn.Embedding` | neural_reranker.py (category + duration bucket) |
| `nn.MultiheadAttention` | neural_reranker.py (self-attention + cross-attention) |
| `nn.TransformerEncoderLayer` | neural_reranker.py (refinement layer) |
| `nn.LayerNorm`, residual connections | neural_reranker.py |
| Padding masks (boolean) | neural_reranker.py, dataset.py |
| `Dataset`, `DataLoader`, custom `collate_fn` | dataset.py |
| Training loop (zero_grad / backward / step) | trainer.py |
| `torch.amp.autocast`, `GradScaler` | trainer.py (mixed precision) |
| `OneCycleLR`, `clip_grad_norm_` | trainer.py |
| Early stopping, checkpointing | trainer.py |
| `model.train()` / `model.eval()`, `torch.no_grad()` | trainer.py |
