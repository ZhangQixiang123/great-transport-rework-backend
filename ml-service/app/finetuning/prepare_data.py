"""Prepare training data for LoRA fine-tuning from historical transport videos.

Converts the database of 7,700+ videos into instruction-tuning examples
that teach the model what makes a good Bilibili transport.
"""
import json
import logging
import math
import os
from typing import List, Dict, Optional, Tuple

import numpy as np

from ..db.database import Database
from ..training.features import load_regression_data

logger = logging.getLogger(__name__)

SYSTEM_MESSAGE = (
    "You are an expert at predicting YouTube-to-Bilibili transport success. "
    "Given a video's metadata and context about similar past transports, "
    "predict how many views it will get on Bilibili and explain your reasoning."
)

USER_TEMPLATE = """\
Predict the Bilibili performance of this transported video.

## Video Info
- Title: {title}
- Duration: {duration}s
- YouTube Views: {yt_views:,}
- YouTube Likes: {yt_likes:,}
- Category ID: {category_id}

## Similar Past Transports
{similar_text}

Respond with JSON:
{{
  "predicted_log_views": <float>,
  "predicted_views": <int>,
  "confidence": <float 0.0-1.0>,
  "label": "<failed|standard|successful|viral>",
  "reasoning": "<explanation>"
}}"""

ASSISTANT_TEMPLATE = """\
{{
  "predicted_log_views": {log_views:.2f},
  "predicted_views": {views},
  "confidence": {confidence:.2f},
  "label": "{label}",
  "reasoning": "{reasoning}"
}}"""


def _label_from_log_views(
    log_views: float,
    p25: float = 7.6,
    p75: float = 10.3,
    p95: float = 12.2,
) -> str:
    if log_views < p25:
        return "failed"
    elif log_views < p75:
        return "standard"
    elif log_views < p95:
        return "successful"
    else:
        return "viral"


def _generate_reasoning(video, log_views: float, label: str) -> str:
    """Generate a brief reasoning based on the video's actual performance."""
    views = int(math.expm1(log_views))

    if label == "viral":
        return (
            f"This video achieved exceptional performance with {views:,} views, "
            f"indicating strong audience resonance and shareability on Bilibili."
        )
    elif label == "successful":
        return (
            f"Solid performance at {views:,} views. The content translates well "
            f"to the Chinese audience with good engagement potential."
        )
    elif label == "standard":
        return (
            f"Average performance at {views:,} views. The content has moderate "
            f"appeal but doesn't stand out significantly."
        )
    else:
        return (
            f"Below average at {views:,} views. The content may lack cultural "
            f"relevance or face strong competition on Bilibili."
        )


def prepare_training_data(
    db: Database,
    model_dir: str = "models",
    output_path: Optional[str] = None,
    train_ratio: float = 0.8,
) -> Tuple[str, str, Dict]:
    """Prepare instruction-tuning data from historical videos.

    Args:
        db: Database connection.
        model_dir: Directory with embedder and vector store.
        output_path: Base path for output files (default: model_dir/finetune_).
        train_ratio: Fraction of data for training (rest for validation).

    Returns:
        Tuple of (train_path, val_path, stats_dict).
    """
    if output_path is None:
        output_path = os.path.join(model_dir, "finetune")

    # Load videos
    videos, targets, yt_stats_map = load_regression_data(db)
    if len(videos) < 100:
        raise ValueError(f"Need at least 100 videos, got {len(videos)}")

    logger.info("Preparing fine-tuning data from %d videos", len(videos))

    # Load VectorStore for similar video context
    vector_store = None
    embedder = None
    vs_path = os.path.join(model_dir, "vector_store.npz")
    emb_path = os.path.join(model_dir, "embedder.pt")

    if os.path.exists(vs_path) and os.path.exists(emb_path):
        try:
            from ..embeddings.vector_store import VectorStore
            from ..embeddings.model import TitleEmbedder
            vector_store = VectorStore.load(vs_path)
            embedder = TitleEmbedder.load(emb_path)
            logger.info("Loaded VectorStore + embedder for context generation")
        except Exception as e:
            logger.warning("Could not load VectorStore/embedder: %s", e)

    # Compute percentiles for balanced sampling
    p25 = float(np.percentile(targets, 25))
    p75 = float(np.percentile(targets, 75))
    p95 = float(np.percentile(targets, 95))

    # Batch-encode all titles at once for VectorStore lookup
    all_similar_texts = ["No similar video data available."] * len(videos)
    if vector_store is not None and embedder is not None:
        try:
            logger.info("Batch-encoding %d titles for VectorStore lookup...", len(videos))
            all_titles = [v.title for v in videos]
            all_embeddings = embedder.encode(all_titles, batch_size=128)
            logger.info("Querying VectorStore for similar videos...")
            for i, (video, emb) in enumerate(zip(videos, all_embeddings)):
                similar = vector_store.query_detailed(
                    emb, top_k=5,
                    exclude_bvid=video.bvid,
                    exclude_channel=video.bilibili_uid,
                )
                if similar:
                    lines = []
                    for sv in similar:
                        sv_views = int(math.expm1(sv["log_views"]))
                        lines.append(
                            f"- Similarity={sv['similarity']:.2f}, "
                            f"Views={sv_views:,}"
                        )
                    all_similar_texts[i] = "\n".join(lines)
        except Exception as e:
            logger.warning("VectorStore lookup failed: %s", e)

    # Generate training examples
    examples = []
    for i, (video, target) in enumerate(zip(videos, targets)):
        yt = yt_stats_map.get(video.bvid, {})
        yt_views = yt.get("yt_views", 0) if yt else 0
        yt_likes = yt.get("yt_likes", 0) if yt else 0
        category_id = yt.get("yt_category_id", 0) if yt else 0

        similar_text = all_similar_texts[i]

        label = _label_from_log_views(target, p25, p75, p95)
        views = int(math.expm1(target))
        reasoning = _generate_reasoning(video, target, label)

        # Compute confidence based on how many similar videos we have
        confidence = 0.7 if similar_text != "No similar video data available." else 0.5

        user_msg = USER_TEMPLATE.format(
            title=video.title,
            duration=video.duration,
            yt_views=yt_views,
            yt_likes=yt_likes,
            category_id=category_id,
            similar_text=similar_text,
        )

        assistant_msg = ASSISTANT_TEMPLATE.format(
            log_views=target,
            views=views,
            confidence=confidence,
            label=label,
            reasoning=reasoning,
        )

        examples.append({
            "messages": [
                {"role": "system", "content": SYSTEM_MESSAGE},
                {"role": "user", "content": user_msg},
                {"role": "assistant", "content": assistant_msg},
            ]
        })

    # Shuffle and split
    rng = np.random.RandomState(42)
    indices = rng.permutation(len(examples))
    split_idx = int(len(examples) * train_ratio)
    train_indices = indices[:split_idx]
    val_indices = indices[split_idx:]

    train_path = f"{output_path}_train.jsonl"
    val_path = f"{output_path}_val.jsonl"

    os.makedirs(os.path.dirname(train_path) or ".", exist_ok=True)

    with open(train_path, "w", encoding="utf-8") as f:
        for idx in train_indices:
            f.write(json.dumps(examples[idx], ensure_ascii=False) + "\n")

    with open(val_path, "w", encoding="utf-8") as f:
        for idx in val_indices:
            f.write(json.dumps(examples[idx], ensure_ascii=False) + "\n")

    # Compute label distribution
    labels = [_label_from_log_views(targets[i], p25, p75, p95)
              for i in range(len(targets))]
    label_counts = {}
    for l in labels:
        label_counts[l] = label_counts.get(l, 0) + 1

    stats = {
        "total_videos": len(videos),
        "train_examples": len(train_indices),
        "val_examples": len(val_indices),
        "label_distribution": label_counts,
        "percentiles": {"p25": p25, "p75": p75, "p95": p95},
        "train_path": train_path,
        "val_path": val_path,
    }

    logger.info(
        "Prepared %d train + %d val examples. Labels: %s",
        len(train_indices), len(val_indices), label_counts,
    )
    return train_path, val_path, stats
