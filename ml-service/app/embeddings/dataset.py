"""PyTorch Dataset and DataLoader for video title regression.

Concepts: torch.utils.data.Dataset, __getitem__, DataLoader, tokenizers.
"""
import math
from typing import List, Tuple

import torch
from torch.utils.data import DataLoader, Dataset, Subset
from transformers import AutoTokenizer

from ..db.database import CompetitorVideo


class VideoTitleDataset(Dataset):
    """Dataset of video titles with log(views) regression targets.

    Each item returns tokenized title tensors and a scalar target.
    """

    def __init__(
        self,
        videos: List[CompetitorVideo],
        tokenizer_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        max_length: int = 128,
    ):
        self.videos = videos
        self.tokenizer = AutoTokenizer.from_pretrained(tokenizer_name)
        self.max_length = max_length

    def __len__(self) -> int:
        return len(self.videos)

    def __getitem__(self, idx: int) -> dict:
        video = self.videos[idx]
        encoding = self.tokenizer(
            video.title,
            max_length=self.max_length,
            padding="max_length",
            truncation=True,
            return_tensors="pt",
        )
        target = math.log1p(max(video.views, 0))
        return {
            "input_ids": encoding["input_ids"].squeeze(0),
            "attention_mask": encoding["attention_mask"].squeeze(0),
            "target": torch.tensor(target, dtype=torch.float32),
        }


def create_dataloaders(
    videos: List[CompetitorVideo],
    train_idx: List[int],
    val_idx: List[int],
    batch_size: int = 64,
    tokenizer_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    max_length: int = 128,
    pin_memory: bool = False,
) -> Tuple[DataLoader, DataLoader]:
    """Create train and validation DataLoaders from index splits.

    Args:
        videos: Full list of videos.
        train_idx: Indices for training set.
        val_idx: Indices for validation set.
        batch_size: Batch size for DataLoaders.
        tokenizer_name: HuggingFace tokenizer to use.
        max_length: Max token length.
        pin_memory: If True, use page-locked memory for faster GPU transfer.

    Returns:
        Tuple of (train_loader, val_loader).
    """
    dataset = VideoTitleDataset(videos, tokenizer_name, max_length)
    train_subset = Subset(dataset, train_idx)
    val_subset = Subset(dataset, val_idx)
    train_loader = DataLoader(
        train_subset, batch_size=batch_size, shuffle=True, num_workers=0,
        pin_memory=pin_memory,
    )
    val_loader = DataLoader(
        val_subset, batch_size=batch_size, shuffle=False, num_workers=0,
        pin_memory=pin_memory,
    )
    return train_loader, val_loader
