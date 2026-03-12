# PyTorch Embedding Fine-Tuning + RAG Architecture

## Problem

The current LightGBM model has **CV R2 = -0.37** on unseen channels (correlation 0.27). It memorizes channel-level view baselines but learns nothing about video content. Title embeddings are PCA-crushed from 384d to 20d (46.6% variance retained). There is no mechanism to say "videos like this one historically got X views on Bilibili."

## Solution

Add two PyTorch-powered capabilities:

1. **Fine-tuned title embeddings** — train a projection head (and optionally the backbone) so embeddings organize by "Bilibili appeal" instead of generic semantics
2. **RAG vector store** — at prediction time, retrieve the k most similar past videos and use their actual Bilibili views as features

LightGBM remains the final ranker. Features go from 43 to 48 (+5 RAG features).

---

## Architecture Diagram

```
===================================================================================
                        TRAINING FLOW (offline, run once)
===================================================================================

  SQLite DB (data.db)
  7,743 videos, 31 channels
       |
       v
  +-----------------------------+
  | load_regression_data(db)    |    <-- existing function
  | returns List[CompetitorVideo], targets, yt_stats_map
  +-----------------------------+
       |
       v
  +-----------------------------+
  | VideoTitleDataset            |    <-- NEW (PyTorch Dataset)
  | __getitem__(idx):            |
  |   title -> tokenize          |
  |   log1p(views) -> target     |
  +-----------------------------+
       |
       v
  +-----------------------------+
  | DataLoader                   |    <-- PyTorch DataLoader
  | batch_size=64, shuffle=True  |
  | num_workers=0 (Windows)      |
  +-----------------------------+
       |
       v
  +-----------------------------+
  | TitleEmbedder                |    <-- NEW (nn.Module)
  | backbone: MiniLM-L12 (384d) |    frozen or fine-tuned
  | projection: 384 -> 128d     |    always trained
  +-----------------------------+
       |
       v
  +-----------------------------+
  | RegressionHead               |    <-- NEW (nn.Module, auxiliary)
  | Linear(128, 64) -> ReLU      |
  | -> Linear(64, 1)             |
  +-----------------------------+
       |
       v
  +-----------------------------+
  | MSE Loss                     |    criterion(predicted, log_views)
  | loss.backward()              |    <-- automatic differentiation
  | optimizer.step()             |    <-- update weights
  +-----------------------------+
       |
       | (repeat for epochs, early stopping)
       v
  +-----------------------------+
  | Save: models/embedder.pt     |    fine-tuned TitleEmbedder weights
  +-----------------------------+
       |
       v
  +-----------------------------+
  | Embed ALL 7,743 titles       |    TitleEmbedder.encode(all_titles)
  | -> numpy [7743, 128]         |    full-dimensional embeddings
  +-----------------------------+
       |
       +--------------------+--------------------+
       |                                         |
       v                                         v
  +-----------------------------+   +-----------------------------+
  | PCA reduce 128d -> 20d      |   | Build VectorStore           |
  | -> title_emb_0..19 features |   | store embeddings + views    |
  | Save: title_embeddings.npz  |   | Save: vector_store.npz     |
  +-----------------------------+   +-----------------------------+
       |                                         |
       +--------------------+--------------------+
                            |
                            v
  +-----------------------------+
  | extract_features_dataframe() |    <-- existing, extended
  | 10 pre-upload + 3 clickbait  |
  | + 7 YouTube + 3 additional   |
  | + 20 title_emb + 5 RAG       |    = 48 features total
  +-----------------------------+
                            |
                            v
  +-----------------------------+
  | LightGBM train_model()      |    <-- existing trainer
  | on 48 features               |
  +-----------------------------+


===================================================================================
                        INFERENCE FLOW (per new video)
===================================================================================

  New YouTube video (title string)
       |
       v
  +-----------------------------+
  | TitleEmbedder.encode(title)  |    Load from models/embedder.pt
  | -> [128] embedding           |
  +-----------------------------+
       |
       +--------------------+--------------------+
       |                                         |
       v                                         v
  +-----------------------------+   +-----------------------------+
  | PCA transform               |   | VectorStore.query(emb,     |
  | 128d -> 20d                 |   |   top_k=20)                |
  | -> title_emb_0..19          |   | cosine similarity search   |
  +-----------------------------+   | -> 5 RAG features          |
       |                         |   +-----------------------------+
       |                         |                |
       v                         v                v
  +--------------------------------------------------+
  | extract_features_single()                          |
  | 10 + 3 + 7 + 3 + 20 + 5 = 48 features            |
  +--------------------------------------------------+
                            |
                            v
  +-----------------------------+
  | LightGBM.predict()          |    existing ranker
  | -> predicted_log_views      |
  | -> label (failed/standard/  |
  |    successful/viral)        |
  +-----------------------------+


===================================================================================
                     MODULE CONNECTION MAP
===================================================================================

  Existing files (modify):            New files (create):
  ========================            ===================

  app/training/features.py  <------+  app/embeddings/__init__.py
    + RAG_FEATURES (5 new)   |     |  app/embeddings/dataset.py      VideoTitleDataset
    + rag_features param     |     |  app/embeddings/model.py        TitleEmbedder
                             |     |  app/embeddings/trainer.py      fine_tune_embeddings()
  app/training/trainer.py  <-+     |  app/embeddings/vector_store.py VectorStore
    + embed + RAG before LGB |     |
                             |     |
  app/models/ranker.py  <----+     |
    + load embedder + store  |     |
    + auto-embed at inference|     |
                             |     |
  app/cli.py <---------------+     |
    + fine-tune-embeddings cmd     |
                             +-----+
```

---

## PyTorch Concepts Roadmap

Each component teaches specific PyTorch fundamentals, ordered from easiest to hardest:

### Level 1: Data Pipeline (dataset.py)

```
+------------------------------------------------------------------+
|  PyTorch Data Pipeline                                            |
|                                                                   |
|  Dataset                  DataLoader                              |
|  +------------------+     +------------------+                    |
|  | __len__() -> int |     | wraps Dataset    |                    |
|  | __getitem__(idx) |---->| batches samples  |                    |
|  |   -> dict of     |     | shuffles         |                    |
|  |      tensors     |     | multi-worker     |                    |
|  +------------------+     +------------------+                    |
|                                    |                              |
|  What you learn:                   v                              |
|  - torch.Tensor basics      [batch_size, seq_len] tensors        |
|  - Tokenizer usage          ready for model.forward()            |
|  - Data/target pairing                                           |
+------------------------------------------------------------------+
```

**`VideoTitleDataset.__getitem__(idx)`** returns:
```python
{
    "input_ids":      torch.LongTensor([101, 2054, ...]),   # [max_length]
    "attention_mask":  torch.LongTensor([1, 1, 1, ..., 0]), # [max_length]
    "target":          torch.FloatTensor(7.6)                # scalar (log views)
}
```

**DataLoader** automatically collates these into batches:
```python
{
    "input_ids":      [batch_size, max_length],    # stacked
    "attention_mask":  [batch_size, max_length],    # stacked
    "target":          [batch_size]                  # stacked
}
```

### Level 2: Neural Network Module (model.py)

```
+------------------------------------------------------------------+
|  nn.Module Architecture: TitleEmbedder                            |
|                                                                   |
|  input_ids [B, S]  attention_mask [B, S]                          |
|       |                   |                                       |
|       +--------+----------+                                       |
|                |                                                  |
|                v                                                  |
|  +---------------------------+                                    |
|  | self.backbone             |  <-- pretrained transformer        |
|  | AutoModel (MiniLM-L12)   |      384d hidden size              |
|  | requires_grad = False     |      (frozen by default)           |
|  +---------------------------+                                    |
|                |                                                  |
|                v                                                  |
|       [B, S, 384]   (token-level embeddings)                     |
|                |                                                  |
|                v                                                  |
|  +---------------------------+                                    |
|  | Mean Pooling              |  avg over tokens, mask padding     |
|  | (attention_mask weighted) |                                    |
|  +---------------------------+                                    |
|                |                                                  |
|                v                                                  |
|          [B, 384]    (sentence-level embedding)                   |
|                |                                                  |
|                v                                                  |
|  +---------------------------+                                    |
|  | self.projection            |  <-- nn.Sequential               |
|  | Linear(384, 128)           |     (always trainable)           |
|  | ReLU()                     |                                  |
|  | Dropout(0.1)               |                                  |
|  | Linear(128, 128)           |                                  |
|  +---------------------------+                                    |
|                |                                                  |
|                v                                                  |
|          [B, 128]    (final embedding)                            |
|                                                                   |
|  What you learn:                                                  |
|  - nn.Module subclassing (super().__init__(), forward())         |
|  - Layer composition (nn.Sequential, nn.Linear, nn.ReLU)         |
|  - Parameter freezing (requires_grad = False)                    |
|  - Tensor shapes through each layer                              |
|  - state_dict() save/load pattern                                |
+------------------------------------------------------------------+
```

**Key PyTorch pattern — nn.Module lifecycle**:
```python
class TitleEmbedder(nn.Module):
    def __init__(self, ...):
        super().__init__()           # 1. Register with PyTorch
        self.backbone = AutoModel..  # 2. Define layers as attributes
        self.projection = nn.Sequential(...)

    def forward(self, input_ids, attention_mask):
        # 3. Define computation (called via model(inputs))
        hidden = self.backbone(input_ids, attention_mask)
        pooled = mean_pool(hidden)
        return self.projection(pooled)

    def save(self, path):
        torch.save(self.state_dict(), path)  # 4. Serialize weights

    @classmethod
    def load(cls, path):
        model = cls(...)
        model.load_state_dict(torch.load(path))  # 5. Restore weights
        return model
```

### Level 3: Training Loop (trainer.py)

```
+------------------------------------------------------------------+
|  The PyTorch Training Loop                                        |
|  (The most important pattern in all of PyTorch)                  |
|                                                                   |
|  for epoch in range(max_epochs):                                  |
|                                                                   |
|    model.train()        # Enable dropout, batch norm training     |
|    |                                                              |
|    for batch in train_loader:                                     |
|      |                                                            |
|      |  +------------------+                                      |
|      +->| optimizer.       |  Step 1: CLEAR old gradients         |
|         | zero_grad()      |  (gradients accumulate by default!)  |
|         +------------------+                                      |
|                |                                                  |
|                v                                                  |
|         +------------------+                                      |
|         | emb = model(     |  Step 2: FORWARD pass                |
|         |   input_ids,     |  PyTorch records all operations      |
|         |   attention_mask)|  into a computation graph            |
|         | pred = head(emb) |                                      |
|         +------------------+                                      |
|                |                                                  |
|                v                                                  |
|         +------------------+                                      |
|         | loss = criterion( |  Step 3: COMPUTE loss                |
|         |   pred, target)  |  How wrong is the prediction?        |
|         +------------------+                                      |
|                |                                                  |
|                v                                                  |
|         +------------------+                                      |
|         | loss.backward()  |  Step 4: BACKWARD pass               |
|         |                  |  Traverse computation graph in        |
|         |                  |  reverse, computing d(loss)/d(param)  |
|         |                  |  for every trainable parameter        |
|         +------------------+                                      |
|                |                                                  |
|                v                                                  |
|         +------------------+                                      |
|         | optimizer.step() |  Step 5: UPDATE weights               |
|         |                  |  param -= lr * gradient               |
|         |                  |  (Adam adapts lr per-parameter)       |
|         +------------------+                                      |
|                                                                   |
|    model.eval()         # Disable dropout for validation          |
|    with torch.no_grad():  # Don't track gradients (saves memory)  |
|      validate(val_loader)                                         |
|                                                                   |
|    scheduler.step(val_loss)   # Reduce LR if plateauing           |
|    if no_improvement(patience): break   # Early stopping          |
|                                                                   |
|  What you learn:                                                  |
|  - The sacred 5-step loop (zero_grad->forward->loss->backward->step)
|  - Automatic differentiation (loss.backward())                   |
|  - train() vs eval() mode                                        |
|  - torch.no_grad() for inference                                 |
|  - Learning rate scheduling                                      |
|  - Early stopping                                                |
|  - Device management (.to(device))                               |
+------------------------------------------------------------------+
```

**Training flow with actual tensor shapes**:
```
 input_ids    attention_mask     target
 [64, 128]     [64, 128]        [64]          (batch of 64 titles)
     |              |               |
     v              v               |
 TitleEmbedder.forward()            |
     |                              |
     v                              |
 embeddings [64, 128]               |
     |                              |
     v                              |
 RegressionHead.forward()           |
     |                              |
     v                              v
 predictions [64]              targets [64]
     |                              |
     +----------+-------------------+
                |
                v
          MSE Loss (scalar)
                |
                v
          loss.backward()
       (computes all gradients)
                |
                v
          optimizer.step()
       (updates all parameters)
```

### Level 4: VectorStore (vector_store.py) — No PyTorch

```
+------------------------------------------------------------------+
|  VectorStore: RAG Similar-Video Retrieval                         |
|  (Pure numpy — no PyTorch needed)                                |
|                                                                   |
|  BUILD (offline):                                                 |
|  +---------------------------+                                    |
|  | embeddings  [7743, 128]   |  from TitleEmbedder.encode()      |
|  | bvids       [7743]        |  video IDs                        |
|  | log_views   [7743]        |  known Bilibili log(views)        |
|  | channel_ids [7743]        |  for exclude_channel              |
|  | norms       [7743, 1]     |  precomputed for cosine sim       |
|  +---------------------------+                                    |
|                                                                   |
|  QUERY (per video):                                               |
|  query_emb [128]                                                  |
|       |                                                           |
|       v                                                           |
|  cosine_sim = query @ stored.T / (|q| * |s|)                     |
|       |                                                           |
|       v                                                           |
|  top_k = argpartition(similarities, k=20)                         |
|       |                                                           |
|       v  (exclude own bvid + own channel during training)         |
|  +---------------------------+                                    |
|  | RAG Features:             |                                    |
|  | rag_similar_median_log_v  |  median of top-20 views            |
|  | rag_similar_mean_log_v    |  mean of top-20 views              |
|  | rag_similar_std_log_v     |  std of top-20 views               |
|  | rag_similar_max_log_v     |  max of top-20 views               |
|  | rag_top5_mean_log_v       |  mean of top-5 most similar        |
|  +---------------------------+                                    |
|                                                                   |
|  Why these features help:                                         |
|  "Videos with similar titles to this one got median 50K views"    |
|  -> directly addresses the cold-start problem for new channels    |
+------------------------------------------------------------------+
```

---

## Feature Integration

### Current features (43):
```
PRE_UPLOAD_FEATURES (10):  duration, duration_bucket, title_length,
                           title_has_number, description_length,
                           publish_hour_sin/cos, publish_dow_sin/cos,
                           has_youtube_source

CLICKBAIT_FEATURES (3):    title_exclamation_count, title_question_count,
                           title_caps_ratio

YOUTUBE_FEATURES (7):      yt_log_views, yt_log_likes, yt_log_comments,
                           yt_duration_seconds, yt_like_view_ratio,
                           yt_comment_view_ratio, yt_category_id

ADDITIONAL_FEATURES (3):   yt_tag_count, yt_upload_delay_days, yt_stats_imputed

EMBEDDING_FEATURES (20):   title_emb_0 .. title_emb_19  (PCA-reduced)
```

### New features (+5 = 48 total):
```
RAG_FEATURES (5):          rag_similar_median_log_views
                           rag_similar_mean_log_views
                           rag_similar_std_log_views
                           rag_similar_max_log_views
                           rag_top5_mean_log_views
```

The 20 `title_emb_*` features are now generated from the **fine-tuned** embedder (PCA-reduced from 128d) instead of the frozen pretrained model (PCA from 384d). This means embeddings are optimized for view prediction, not generic semantic similarity.

---

## Data Leakage Prevention

During training, the VectorStore query must **exclude the video's own channel**:

```
Training video from Channel A:
  VectorStore.query(emb, exclude_channel="channel_A")
  -> only returns videos from Channels B, C, D, ...
  -> prevents the model from learning "this channel's videos get X views"
  -> forces it to learn content-level patterns

At inference (new video, unknown channel):
  VectorStore.query(emb)
  -> searches all stored videos
  -> "videos with titles similar to yours got median 50K views"
```

This is critical. Without `exclude_channel`, the RAG features would just leak channel identity (similar titles within the same channel) and the model would still fail on unseen channels.

---

## File Structure

```
ml-service/
  app/
    embeddings/                     <-- NEW PACKAGE
      __init__.py                    exports public API
      dataset.py                     VideoTitleDataset, create_dataloaders
      model.py                       TitleEmbedder (nn.Module)
      trainer.py                     fine_tune_embeddings(), RegressionHead
      vector_store.py                VectorStore
    training/
      features.py                    MODIFY: +RAG_FEATURES, +rag_features param
      trainer.py                     MODIFY: integrate embedder + VectorStore
    models/
      ranker.py                      MODIFY: auto-embed + RAG at inference
    cli.py                           MODIFY: +fine-tune-embeddings command
  tests/
    test_embeddings_dataset.py       NEW
    test_embeddings_model.py         NEW
    test_vector_store.py             NEW
    test_embeddings_trainer.py       NEW
    test_features.py                 MODIFY: feature count 43 -> 48
  models/
    embedder.pt                      NEW artifact (fine-tuned weights)
    vector_store.npz                 NEW artifact (all embeddings for RAG)
    title_embeddings.npz             EXISTING (regenerated from fine-tuned model)
    latest_model.json                EXISTING (LightGBM, now trained on 48 features)
```

---

## Implementation Order

```
Step 1: dataset.py + tests    (PyTorch: Dataset, DataLoader, Tensor basics)
          |
Step 2: model.py + tests      (PyTorch: nn.Module, forward(), save/load)
          |
Step 3: vector_store.py + tests (numpy only — mental break)
          |
Step 4: trainer.py + tests     (PyTorch: training loop, backward, optimizer)
          |
Step 5: features.py changes    (add RAG_FEATURES, update constants)
          |
Step 6: trainer.py changes     (integrate embedder + VectorStore)
          |
Step 7: ranker.py changes      (auto-embed + RAG at inference)
          |
Step 8: cli.py changes         (add fine-tune-embeddings command)
```

Each step is independently testable. Steps 1-4 create the new package. Steps 5-8 integrate it.

---

## Expected Impact

| Metric | Before | After (estimated) |
|--------|--------|-------------------|
| CV R2 | -0.37 | ~0.0 to 0.2 |
| CV Correlation | 0.27 | ~0.40 to 0.55 |
| CV RMSE | 2.28 | ~1.8 to 2.0 |
| Feature count | 43 | 48 |
| Embedding dims | 20 (PCA from 384) | 20 (PCA from 128 fine-tuned) + 5 RAG |

The biggest gain comes from the **RAG features** which give the model content-aware baselines for unseen channels. The fine-tuned embeddings help by making the PCA-reduced dimensions more predictive.

---

## GPU Acceleration (RTX 4080 Laptop)

### Hardware Profile

```
RTX 4080 Laptop: ~12GB VRAM, Ada Lovelace architecture
  - Tensor Cores: native fp16/bf16 acceleration (2x throughput vs fp32)
  - CUDA Cores: 7424
  - Memory bandwidth: 256 GB/s
```

### What the GPU Changes

With 12GB VRAM you can **full fine-tune** the backbone (not just the projection head). This is a much more powerful approach:

| Setting | CPU-only | GPU (frozen backbone) | GPU (full fine-tune) |
|---------|----------|----------------------|---------------------|
| Trainable params | 16K (head only) | 16K (head only) | 33M (all) |
| Batch size | 16-32 | 128-256 | 64-128 |
| Time/epoch | ~2 min | ~5 sec | ~15 sec |
| VRAM usage | N/A | ~2 GB | ~6 GB |
| Learning rate | 1e-3 | 1e-3 | 2e-5 |
| Expected quality | Moderate | Moderate | Best |

### GPU-Specific PyTorch Concepts

#### 1. Mixed Precision Training (`torch.amp`)

RTX 4080 Tensor Cores do fp16 math at 2x the speed of fp32. PyTorch's Automatic Mixed Precision (AMP) handles this transparently:

```
+------------------------------------------------------------------+
|  Mixed Precision Training (AMP)                                   |
|                                                                   |
|  Without AMP (fp32 everywhere):                                  |
|    forward:  fp32 tensors -> fp32 computation -> fp32 output     |
|    backward: fp32 gradients everywhere                           |
|    Speed: 1x                                                     |
|                                                                   |
|  With AMP (mixed fp16/fp32):                                     |
|    forward:  fp16 tensors -> fp16 computation -> fp32 loss       |
|    backward: fp16 gradients -> fp32 weight updates               |
|    Speed: ~1.5-2x on RTX 4080                                   |
|                                                                   |
|  PyTorch handles the casting automatically. You just wrap:       |
|                                                                   |
|    scaler = torch.amp.GradScaler("cuda")                        |
|                                                                   |
|    with torch.amp.autocast("cuda", dtype=torch.float16):        |
|        output = model(inputs)          # runs in fp16            |
|        loss = criterion(output, target) # loss computed in fp32  |
|                                                                   |
|    scaler.scale(loss).backward()       # scale to prevent        |
|    scaler.step(optimizer)              #   fp16 underflow        |
|    scaler.update()                                               |
|                                                                   |
|  What you learn:                                                  |
|  - GradScaler prevents gradient underflow in fp16                |
|  - autocast context manager auto-casts tensor dtypes             |
|  - Some ops stay fp32 (loss, softmax) for numerical stability    |
+------------------------------------------------------------------+
```

#### 2. CUDA Device Management

```
+------------------------------------------------------------------+
|  Device Management                                                |
|                                                                   |
|  # Auto-select best device                                       |
|  device = torch.device("cuda" if torch.cuda.is_available()       |
|                        else "cpu")                                |
|                                                                   |
|  # Move model to GPU (copies all parameters to VRAM)             |
|  model = model.to(device)                                        |
|                                                                   |
|  # Move each batch to GPU inside training loop                   |
|  input_ids = batch["input_ids"].to(device)                       |
|  targets = batch["target"].to(device)                            |
|                                                                   |
|  # CPU<->GPU data flow:                                          |
|  CPU RAM                 GPU VRAM (12 GB)                        |
|  +--------+              +------------------+                     |
|  | Dataset| --batch-->   | input tensors    |                     |
|  | (titles|   .to(cuda)  | model weights    |                     |
|  | views) |              | gradients        |                     |
|  +--------+              | optimizer state   |                     |
|       ^                  +------------------+                     |
|       |                         |                                 |
|       +--- .cpu().numpy() ------+  (move results back for eval)   |
|                                                                   |
|  Key rule: tensors on different devices can't interact.          |
|  model.to("cuda") + input.to("cuda") = OK                       |
|  model.to("cuda") + input on CPU = RuntimeError                  |
+------------------------------------------------------------------+
```

#### 3. DataLoader with `pin_memory`

```
+------------------------------------------------------------------+
|  pin_memory=True  (GPU optimization)                              |
|                                                                   |
|  Normal:    CPU RAM  --copy-->  CPU pinned  --DMA-->  GPU VRAM   |
|  Pinned:    CPU pinned RAM  --------DMA------------>  GPU VRAM   |
|                                                                   |
|  Pinned memory skips one copy step. The DataLoader pre-allocates |
|  page-locked ("pinned") host memory so .to("cuda") is faster.   |
|                                                                   |
|  DataLoader(                                                      |
|      dataset,                                                     |
|      batch_size=128,                                              |
|      shuffle=True,                                                |
|      num_workers=0,       # still 0 on Windows                   |
|      pin_memory=True,     # <-- enable for GPU training          |
|  )                                                                |
|                                                                   |
|  Then in training loop use non_blocking transfer:                 |
|  input_ids = batch["input_ids"].to(device, non_blocking=True)    |
+------------------------------------------------------------------+
```

### GPU Training Loop (Updated)

The training loop with all GPU optimizations:

```python
# Setup
device = torch.device("cuda")
model.to(device)

scaler = torch.amp.GradScaler("cuda")       # AMP scaler

for epoch in range(epochs):
    model.train()

    for batch in train_loader:
        # 1. Move to GPU (non-blocking with pinned memory)
        input_ids = batch["input_ids"].to(device, non_blocking=True)
        attention_mask = batch["attention_mask"].to(device, non_blocking=True)
        targets = batch["target"].to(device, non_blocking=True)

        optimizer.zero_grad()

        # 2. Forward in mixed precision (fp16 on Tensor Cores)
        with torch.amp.autocast("cuda", dtype=torch.float16):
            embeddings = model(input_ids, attention_mask)
            predictions = reg_head(embeddings)
            loss = criterion(predictions, targets)

        # 3. Backward with gradient scaling (prevents fp16 underflow)
        scaler.scale(loss).backward()
        scaler.step(optimizer)
        scaler.update()

    # Validation (no AMP needed, just no_grad)
    model.eval()
    with torch.no_grad():
        for batch in val_loader:
            ...
```

### Full Fine-Tune Mode

With 12GB VRAM, you can unfreeze the backbone for deeper learning:

```
+------------------------------------------------------------------+
|  Frozen vs Full Fine-Tune                                         |
|                                                                   |
|  Frozen backbone (freeze_backbone=True):                         |
|  +---------------------------+                                    |
|  | backbone (33M params)     |  FROZEN  (no gradients)           |
|  +---------------------------+                                    |
|  | projection (50K params)   |  TRAINED                          |
|  +---------------------------+                                    |
|  - Fast: only 50K params update                                  |
|  - Safe: pretrained knowledge preserved                          |
|  - But: embeddings stay generic, not task-specific               |
|                                                                   |
|  Full fine-tune (freeze_backbone=False):                         |
|  +---------------------------+                                    |
|  | backbone (33M params)     |  TRAINED  (small LR: 2e-5)       |
|  +---------------------------+                                    |
|  | projection (50K params)   |  TRAINED  (larger LR: 1e-3)      |
|  +---------------------------+                                    |
|  - Slower: 33M params update                                     |
|  - Needs lower LR to avoid destroying pretrained knowledge       |
|  - Better: backbone adapts to "Bilibili view prediction" task    |
|  - Requires GPU (too slow on CPU)                                |
|                                                                   |
|  Differential learning rates:                                    |
|  optimizer = Adam([                                               |
|      {"params": model.backbone.parameters(), "lr": 2e-5},       |
|      {"params": model.projection.parameters(), "lr": 1e-3},     |
|  ])                                                               |
|  The backbone gets a 50x smaller LR to gently adapt              |
|  while the projection head learns aggressively.                  |
+------------------------------------------------------------------+
```

### Larger Backbone Option

With 12GB VRAM you can also consider a larger model:

```
| Model | Params | Dim | VRAM (train) | Language |
|-------|--------|-----|-------------|----------|
| MiniLM-L12 (current) | 33M | 384 | ~3 GB | multilingual |
| multilingual-e5-base | 278M | 768 | ~8 GB | multilingual, better |
| bge-m3 | 568M | 1024 | ~11 GB | multilingual, best |

MiniLM-L12 is the safe default. If results are promising,
upgrade to e5-base for better multilingual understanding
(Chinese title handling). bge-m3 is the ceiling — fits
in 12GB but leaves little room for large batches.
```

### VRAM Budget Breakdown

```
MiniLM-L12, full fine-tune, batch_size=128:
  Model weights (fp16):      ~66 MB
  Optimizer state (fp32):    ~260 MB   (Adam stores 2x params)
  Activations (fp16):        ~500 MB   (depends on seq_len)
  Gradients (fp16):          ~66 MB
  Batch data:                ~50 MB
  PyTorch overhead:          ~500 MB
  ─────────────────────────────────
  Total:                     ~1.5 GB   (plenty of room)

e5-base, full fine-tune, batch_size=64:
  Model weights (fp16):      ~550 MB
  Optimizer state (fp32):    ~2.2 GB
  Activations (fp16):        ~2 GB
  Gradients (fp16):          ~550 MB
  Batch data:                ~25 MB
  PyTorch overhead:          ~500 MB
  ─────────────────────────────────
  Total:                     ~5.8 GB   (fits comfortably)
```

### Summary of GPU Design Decisions

| Decision | Value | Rationale |
|----------|-------|-----------|
| Default `freeze_backbone` | `False` | GPU makes full fine-tune fast enough |
| Default `batch_size` | 128 | Fits easily in 12GB, good gradient estimates |
| Mixed precision | Always on when CUDA available | 1.5-2x speedup, free |
| `pin_memory` | `True` when CUDA available | Faster CPU→GPU transfer |
| Default learning rate | 2e-5 backbone, 1e-3 projection | Differential LR for fine-tuning |
| `num_workers` | 0 | Windows compatibility (GPU makes this not the bottleneck) |
| Default backbone | MiniLM-L12 (33M) | Good enough, very fast; upgrade path to e5-base |
| Gradient accumulation | Not needed | batch_size=128 fits in memory |
