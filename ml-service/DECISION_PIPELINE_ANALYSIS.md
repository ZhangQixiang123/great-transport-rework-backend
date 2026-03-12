# Decision Pipeline Analysis: Critical Problems

**Date**: 2026-03-08
**Status**: Unresolved — all issues listed below are present in the current codebase.

---

## Pipeline Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Bilibili API   │────>│  LLM (qwen2.5)   │────>│  YouTube API    │
│  Hot search     │     │  Translate CN→EN  │     │  search.list    │
│  keywords       │     │  2-3 queries      │     │  videos.list    │
└─────────────────┘     └──────────────────┘     └────────┬────────┘
                                                          │
                    candidates (English YouTube videos)    │
                                                          v
                        ┌──────────────────┐     ┌─────────────────┐
                        │  LLM (qwen2.5)   │<────│  Dedup filter   │
                        │  Relevance score  │     │  already_seen   │
                        │  HARD GATE <0.5   │     └─────────────────┘
                        └────────┬─────────┘
                                 │ only score >= 0.5 pass
                                 v
                        ┌──────────────────┐
                        │  ML Model        │
                        │  LightGBM (48f)  │
                        │  predict views   │
                        └────────┬─────────┘
                                 │
                                 v
                        ┌──────────────────┐
                        │  Combined Score  │
                        │  0.2×heat        │
                        │  0.4×relevance   │
                        │  0.4×pred_views  │
                        └────────┬─────────┘
                                 │
                                 v
                        ┌──────────────────┐     ┌─────────────────┐
                        │  Pick top N      │────>│  daily_job.py   │
                        │  Save to DB      │     │  download+upload│
                        └──────────────────┘     └─────────────────┘
```

### Component responsibilities

| Component | File | Role |
|-----------|------|------|
| Hotword fetch | `discovery/trending.py` | `bilibili_api.search.get_hot_search_keywords()` |
| Keyword translation | `discovery/llm_scorer.py` `translate_keyword()` | CN keyword → 2-3 EN YouTube search queries |
| YouTube search | `discovery/youtube_search.py` | YouTube Data API v3 search + stats enrichment |
| Relevance scoring | `discovery/llm_scorer.py` `score_relevance()` | LLM scores keyword-video match, hard gate at 0.5 |
| View prediction | `models/ranker.py` `predict_video()` | LightGBM on 48 features → predicted Bilibili log(views) |
| Combined ranking | `discovery/pipeline.py` `_compute_combined_score()` | Weighted sum: 0.2 heat + 0.4 relevance + 0.4 views |
| Orchestrator | `discovery/pipeline.py` `DiscoveryPipeline.run()` | Wires all steps together |
| Daily automation | `daily_job.py` | discover → pick → download → upload → mark |

### Data flow per video candidate

```
YouTubeCandidate {video_id, title(EN), description(EN), views, likes, ...}
        │
        ├──> LLM score_relevance(keyword, candidate) → RelevanceResult
        │         relevance_score: 0.0-1.0
        │         is_relevant: score >= 0.5 (HARD GATE)
        │
        ├──> _make_dummy_video(candidate) → CompetitorVideo
        │         title = EN YouTube title
        │         description = EN YouTube description
        │         publish_time = None
        │         views/likes/coins/... = 0
        │         bilibili_uid = ""
        │
        ├──> _make_yt_stats(candidate) → dict
        │         yt_views, yt_likes, yt_comments, yt_duration, yt_category, yt_tags
        │
        └──> ranker.predict_video(dummy_video, yt_stats) → prediction
                  1. embedder.encode([EN title]) → 128d embedding
                  2. PCA → 20d title_emb_*   ← BROKEN (PCA not loaded)
                  3. VectorStore.query(128d) → 5 RAG features
                  4. extract_features_single() → 48 features
                  5. LightGBM.predict() → log(views)
```

---

## Critical Bugs

### BUG 1: PCA never saved to disk — title embeddings ALL ZERO at inference

**Severity**: Critical
**Files**: `training/trainer.py:177-183`, `models/ranker.py:58,161-162`

During training, the PCA is fitted and used:
```python
# trainer.py:180-181
pca = PCA(n_components=N_EMBEDDING_DIMS)
pca_embs = pca.fit_transform(fine_embs)
```

But this `pca` object is **never serialized**. In the ranker:
```python
# ranker.py:58
self._pca = None   # initialized as None

# ranker.py:161-162 — never enters this branch
if self._pca is not None:
    title_embedding = self._pca.transform(fine_emb)[0]
```

**Result**: At inference, `title_embedding` stays `None`, and all 20 `title_emb_*` features default to 0.0. `title_emb_0` is the #1 most important feature (importance 113,798 — 2.4x more than #2). The model runs blind on its strongest signal.

**Evidence**: Mock run predicted 162 and 245 views ("failed") for videos with 35K and 281K YouTube views — suspiciously low because the dominant features are zeroed out.

**Fix**: Save the PCA object (e.g., `joblib.dump(pca, "models/pca.pkl")`) during training and load it in `ranker._try_load_embedder()`.

---

### BUG 2: LLM hard gate eliminates videos before ML can evaluate them

**Severity**: Critical
**File**: `discovery/pipeline.py:173-182`

```python
relevance = self.scorer.score_relevance(kw.keyword, candidate)
if not relevance.is_relevant:    # score < 0.5
    continue                      # DROPPED — ML never sees this video
```

The LLM (a 7B local model making subjective judgments) has absolute veto power. A video the ML model would predict as "viral" is killed if the LLM gives it 0.49.

Then relevance is weighted **again** in the combined score:
```python
combined = 0.2 * heat + 0.4 * relevance + 0.4 * predicted_views
```

Relevance is double-counted: once as a binary gate, once as a 40% weight. For surviving videos, relevance is compressed to [0.5, 1.0] (only 0.2 effective range), so it barely differentiates among survivors.

**Fix**: Remove the hard gate. Let all candidates through to ML prediction. Use relevance as a continuous signal in the combined score only.

---

### BUG 3: `yt_upload_delay_days` always 0 at discovery time

**Severity**: High
**Files**: `discovery/pipeline.py:29-46`, `training/features.py:232-239`

`_make_dummy_video()` sets `publish_time=None`. In feature extraction:
```python
if yt_pub and video.publish_time:   # publish_time is None → False
    delay = (v_time - y_time).days
else:
    features["yt_upload_delay_days"] = 0.0   # always hits this branch
```

This is the **#4 most important feature** (importance 18,044). Value 0.0 means "uploaded to Bilibili instantly after YouTube" — a condition that almost never occurs in training data, introducing systematic bias.

**Fix**: Estimate delay as `(now - yt_published_at).days` since the video would be uploaded soon after discovery.

---

## Architectural Flaws

### FLAW 1: English titles at inference, Chinese titles in training

**Severity**: High
**Files**: `embeddings/trainer.py`, `embeddings/model.py`, `embeddings/vector_store.py`, `models/ranker.py`

The entire embedding + RAG system was trained on Chinese Bilibili titles:
- Fine-tuned projection head: trained on 7,743 Chinese titles
- PCA: fitted on Chinese-text projections
- VectorStore: indexes 7,743 Chinese title embeddings

At discovery, input is English YouTube titles. The multilingual backbone (`paraphrase-multilingual-MiniLM-L12-v2`) provides some cross-lingual signal, but:
- The projection head was only ever trained on Chinese text — its behavior on English input is unvalidated
- RAG cosine similarity is cross-lingual (English query vs Chinese database) — much noisier than same-language
- RAG features are #2 and #3 most important (importance 47,692 and 45,188)

**Impact**: The two most powerful feature groups (title embeddings + RAG) operate on out-of-distribution input.

---

### FLAW 2: `description_length` measures wrong thing

**Severity**: Medium
**File**: `discovery/pipeline.py:33`

```python
description=candidate.description,  # English YouTube description
```

Training data: `description_length` = Chinese Bilibili descriptions (typically 50-500 chars).
Discovery: `description_length` = English YouTube descriptions (typically 200-5000 chars).

This is the **#6 most important feature** (importance 15,421). The model learned patterns about Chinese description lengths but receives English description lengths at inference — a different distribution.

---

### FLAW 3: LLM translation errors cascade through the entire pipeline

**Severity**: High
**File**: `discovery/llm_scorer.py:117-158`

Observed in mock run: "外长记者会精华版" (Chinese Foreign Minister Wang Yi's press conference highlights) was translated to "US Secretary of State press conference" — factually wrong. The Chinese keyword is about China's foreign minister, not America's.

Cascade effect:
1. Wrong translation → wrong YouTube search queries
2. Wrong queries → irrelevant YouTube results
3. Irrelevant results → LLM scores them low → all filtered out by hard gate
4. Zero useful recommendations from this keyword

There is no retry strategy, no alternative query generation, no translation validation. One bad LLM call wastes the entire keyword.

---

## Design Problems

### DESIGN 1: No feedback loop

The system never learns from outcomes:
- Videos uploaded to Bilibili that got few views → no signal fed back to scoring
- Videos filtered out by LLM that might have been good → never discovered
- Combined score weights (0.2/0.4/0.4) are hardcoded, never calibrated against actual upload performance
- Relevance threshold (0.5) is arbitrary, never validated

---

### DESIGN 2: ML model overweighted given its accuracy

The ML model gets 40% weight in the combined score, but its CV performance on unseen channels is:
- R2 = -0.065 (barely better than predicting the mean)
- Correlation = 0.44 (moderate ranking signal)

With Bug #1 (embeddings zeroed), real-world performance is even worse. The 40% weight overstates confidence in predictions that are essentially noise for unseen content.

Even after fixing Bug #1, the ML model was trained on Bilibili-native content with Chinese metadata. Discovery candidates are YouTube videos with English metadata — a distribution the model has never seen. The 40% weight should be reconsidered.

---

### DESIGN 3: YouTube search ordering creates implicit bias

```python
# youtube_search.py:56
"order": "relevance"   # YouTube's relevance, not Bilibili potential
```

Combined with `max_results=5`, only YouTube's top-5 results per query are evaluated. YouTube's "relevance" optimizes for YouTube engagement, not Bilibili transport potential. High-view YouTube videos that have zero Bilibili appeal rank above niche videos with genuine cross-platform potential.

---

## Feature status at discovery time vs training time

| Feature | Training | Discovery | Problem |
|---------|----------|-----------|---------|
| `title_emb_0..19` (#1) | PCA of Chinese embeddings | **ALL ZEROS** (PCA not loaded) | BUG 1 |
| `rag_*` (#2,#3) | Chinese-Chinese similarity | English-Chinese similarity | FLAW 1 |
| `yt_upload_delay_days` (#4) | Real delay (days) | **Always 0.0** | BUG 3 |
| `yt_log_views` (#5) | YouTube views | YouTube views | OK |
| `description_length` (#6) | Chinese Bilibili desc | English YouTube desc | FLAW 2 |
| `yt_like_view_ratio` (#7) | YouTube ratio | YouTube ratio | OK |
| `yt_log_likes` (#8) | YouTube likes | YouTube likes | OK |
| `title_length` (#9) | Chinese title chars | English title chars | Minor mismatch |
| `title_caps_ratio` (#10) | Chinese text (low caps) | English text (has caps) | Distribution shift |
| `duration` (#11) | Bilibili duration | YouTube duration | OK (same video) |
| `publish_hour/dow` | Bilibili publish time | **Fixed: noon Wednesday** | Minor |
| `has_youtube_source` | 0 or 1 | **Always 1** | No discrimination |

Of the top 6 most important features, **4 are broken or mismatched** at discovery time.

---

## Interaction between LLM and ML: the core dysfunction

The pipeline uses LLM and ML in series, but their roles conflict:

```
LLM decides IF a video is considered   (binary gate, subjective)
ML decides HOW WELL it would perform    (continuous prediction, data-driven)
Combined score blends both              (but LLM already killed low scorers)
```

Problems with this arrangement:

1. **LLM has veto, ML has no override**: The LLM can kill any video. The ML model can never resurrect a video the LLM rejected. This makes the ML model's contribution conditional on the LLM's approval — an inferior system can override a (potentially) superior one.

2. **Different evaluation criteria**: The LLM scores "topic relevance to keyword." The ML model predicts "Bilibili view count." These are correlated but not the same. A loosely related video with viral potential (funny, high production value, clickbait title) might score low on relevance but high on views. The current architecture cannot discover such videos.

3. **LLM operates on metadata, ML on features**: The LLM sees title + description text and makes a judgment call. The ML model sees 48 numerical features extracted from the same metadata. They're analyzing the same information through different lenses, but only the LLM's conclusion can eliminate candidates.

4. **No information flows from ML to LLM**: The LLM doesn't know what the ML model thinks. It can't say "this video seems only loosely related, but videos like this tend to do well on Bilibili." The two systems are isolated.

---

## Recommended priority for fixes

1. **Save and load PCA** — trivial fix, recovers the #1 feature group
2. **Remove LLM hard gate** — let all candidates reach ML, use relevance as continuous weight only
3. **Estimate upload delay** — use `(now - yt_published_at).days` instead of 0
4. **Include translated Chinese title** — run LLM title translation BEFORE ML prediction, use Chinese title for embeddings/RAG instead of English title
5. **Calibrate combined score weights** — use historical upload data to set weights empirically
6. **Add feedback loop** — track uploaded video performance, retrain periodically
