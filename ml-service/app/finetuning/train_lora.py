"""LoRA fine-tuning of Qwen 2.5 7B for transport prediction.

Uses HuggingFace transformers + PEFT for efficient fine-tuning.
Requires: transformers, peft, bitsandbytes, trl, datasets.

VRAM requirement: ~12-14GB with 4-bit quantization (fits RTX 4080 16GB).
"""
import json
import logging
import os
from typing import Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_BASE_MODEL = "Qwen/Qwen2.5-7B"
DEFAULT_LORA_CONFIG = {
    "r": 16,
    "lora_alpha": 32,
    "lora_dropout": 0.05,
    "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
    "task_type": "CAUSAL_LM",
}
DEFAULT_TRAINING_ARGS = {
    "num_train_epochs": 3,
    "per_device_train_batch_size": 2,
    "gradient_accumulation_steps": 8,
    "learning_rate": 2e-4,
    "warmup_ratio": 0.03,
    "logging_steps": 10,
    "save_strategy": "epoch",
    "eval_strategy": "epoch",
    "bf16": True,
    "gradient_checkpointing": True,
    "optim": "paged_adamw_8bit",
    "max_grad_norm": 0.3,
    "report_to": "none",
}


def train_lora(
    train_path: str,
    val_path: str,
    output_dir: str,
    base_model: str = DEFAULT_BASE_MODEL,
    lora_config: Optional[Dict] = None,
    training_args: Optional[Dict] = None,
) -> Dict:
    """Fine-tune Qwen 2.5 7B with LoRA on transport prediction data.

    Args:
        train_path: Path to training JSONL file.
        val_path: Path to validation JSONL file.
        output_dir: Directory to save the LoRA adapter.
        base_model: HuggingFace model ID for the base model.
        lora_config: Override LoRA configuration.
        training_args: Override training arguments.

    Returns:
        Dict with training results and metrics.
    """
    try:
        import torch
        from datasets import load_dataset
        from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            BitsAndBytesConfig,
        )
        from trl import SFTConfig, SFTTrainer
    except ImportError as e:
        raise ImportError(
            f"Fine-tuning requires: pip install peft bitsandbytes trl datasets. "
            f"Missing: {e}"
        )

    lora_cfg = {**DEFAULT_LORA_CONFIG, **(lora_config or {})}
    train_cfg = {**DEFAULT_TRAINING_ARGS, **(training_args or {})}

    logger.info("Loading base model: %s", base_model)

    # 4-bit quantization config
    bnb_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.bfloat16,
        bnb_4bit_use_double_quant=True,
    )

    # Load tokenizer
    tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # Load model with 4-bit quantization
    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        quantization_config=bnb_config,
        device_map="auto",
        trust_remote_code=True,
    )
    model = prepare_model_for_kbit_training(model)

    # Apply LoRA
    peft_config = LoraConfig(**lora_cfg)
    model = get_peft_model(model, peft_config)

    trainable_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total_params = sum(p.numel() for p in model.parameters())
    logger.info(
        "LoRA: trainable=%d (%.2f%%), total=%d",
        trainable_params, 100.0 * trainable_params / total_params, total_params,
    )

    # Load datasets
    dataset = load_dataset("json", data_files={
        "train": train_path,
        "validation": val_path,
    })

    def format_chat(example):
        """Format messages into a single string for SFTTrainer."""
        text = tokenizer.apply_chat_template(
            example["messages"],
            tokenize=False,
            add_generation_prompt=False,
        )
        return {"text": text}

    dataset = dataset.map(format_chat, remove_columns=["messages"])

    # Training arguments (SFTConfig extends TrainingArguments with SFT-specific params)
    os.makedirs(output_dir, exist_ok=True)
    args = SFTConfig(
        output_dir=output_dir,
        max_length=512,
        dataset_text_field="text",
        packing=False,
        **train_cfg,
    )

    # Create trainer
    trainer = SFTTrainer(
        model=model,
        train_dataset=dataset["train"],
        eval_dataset=dataset["validation"],
        processing_class=tokenizer,
        args=args,
    )

    # Train
    logger.info("Starting LoRA fine-tuning...")
    train_result = trainer.train()

    # Save the LoRA adapter
    adapter_path = os.path.join(output_dir, "lora_adapter")
    model.save_pretrained(adapter_path)
    tokenizer.save_pretrained(adapter_path)
    logger.info("LoRA adapter saved to %s", adapter_path)

    # Evaluate
    eval_result = trainer.evaluate()

    results = {
        "base_model": base_model,
        "lora_config": lora_cfg,
        "adapter_path": adapter_path,
        "train_loss": train_result.training_loss,
        "eval_loss": eval_result.get("eval_loss"),
        "trainable_params": trainable_params,
        "total_params": total_params,
        "train_samples": len(dataset["train"]),
        "val_samples": len(dataset["validation"]),
    }

    logger.info(
        "Fine-tuning complete. Train loss=%.4f, Eval loss=%.4f",
        results["train_loss"], results.get("eval_loss", 0),
    )
    return results
