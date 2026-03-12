"""Export fine-tuned LoRA model to GGUF and create Ollama model.

Steps:
1. Merge LoRA weights back into base model
2. Convert merged model to GGUF format
3. Create Ollama Modelfile
4. Register model with Ollama
"""
import logging
import os
import subprocess
import tempfile
from typing import Optional

logger = logging.getLogger(__name__)

MODELFILE_TEMPLATE = """\
FROM {gguf_path}
TEMPLATE \"\"\"{{{{ if .System }}}}<|im_start|>system
{{{{ .System }}}}<|im_end|>
{{{{ end }}}}{{{{ if .Prompt }}}}<|im_start|>user
{{{{ .Prompt }}}}<|im_end|>
{{{{ end }}}}<|im_start|>assistant
{{{{ .Response }}}}<|im_end|>\"\"\"
PARAMETER temperature 0.3
PARAMETER top_p 0.9
PARAMETER stop "<|im_end|>"
SYSTEM "You are an expert at predicting YouTube-to-Bilibili transport success. \
Given a video's metadata and context, predict Bilibili performance accurately."
"""


def merge_lora(
    base_model: str,
    adapter_path: str,
    output_path: str,
) -> str:
    """Merge LoRA adapter weights into the base model.

    Args:
        base_model: HuggingFace model ID or path.
        adapter_path: Path to the LoRA adapter.
        output_path: Path to save the merged model.

    Returns:
        Path to the merged model directory.
    """
    try:
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as e:
        raise ImportError(f"Requires: pip install peft transformers. Missing: {e}")

    logger.info("Loading base model: %s", base_model)
    tokenizer = AutoTokenizer.from_pretrained(base_model, trust_remote_code=True)
    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        torch_dtype="auto",
        device_map="cpu",
        trust_remote_code=True,
    )

    logger.info("Loading LoRA adapter: %s", adapter_path)
    model = PeftModel.from_pretrained(model, adapter_path)

    logger.info("Merging weights...")
    model = model.merge_and_unload()

    os.makedirs(output_path, exist_ok=True)
    model.save_pretrained(output_path)
    tokenizer.save_pretrained(output_path)

    logger.info("Merged model saved to %s", output_path)
    return output_path


def convert_to_gguf(
    model_path: str,
    output_path: str,
    quantization: str = "q4_k_m",
) -> str:
    """Convert a HuggingFace model to GGUF format.

    Requires llama-cpp-python or llama.cpp's convert script.

    Args:
        model_path: Path to the merged HuggingFace model.
        output_path: Path for the output GGUF file.
        quantization: Quantization type (q4_k_m, q5_k_m, q8_0, etc.).

    Returns:
        Path to the GGUF file.
    """
    # Try llama.cpp convert script
    convert_script = os.environ.get("LLAMA_CPP_CONVERT", "convert_hf_to_gguf.py")

    try:
        # Step 1: Convert to GGUF (float16)
        fp16_path = output_path.replace(".gguf", "_fp16.gguf")
        subprocess.run(
            ["python", convert_script, model_path,
             "--outfile", fp16_path, "--outtype", "f16"],
            check=True,
            capture_output=True,
            text=True,
        )
        logger.info("Converted to fp16 GGUF: %s", fp16_path)

        # Step 2: Quantize
        quantize_bin = os.environ.get("LLAMA_CPP_QUANTIZE", "llama-quantize")
        subprocess.run(
            [quantize_bin, fp16_path, output_path, quantization],
            check=True,
            capture_output=True,
            text=True,
        )
        logger.info("Quantized to %s: %s", quantization, output_path)

        # Clean up fp16 intermediate
        if os.path.exists(fp16_path) and fp16_path != output_path:
            os.remove(fp16_path)

        return output_path

    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.error(
            "GGUF conversion failed. Ensure llama.cpp is installed. Error: %s", e
        )
        raise RuntimeError(
            "GGUF conversion requires llama.cpp. "
            "Set LLAMA_CPP_CONVERT and LLAMA_CPP_QUANTIZE env vars, "
            "or install llama-cpp-python."
        ) from e


def create_ollama_model(
    gguf_path: str,
    model_name: str = "transport-qwen",
) -> bool:
    """Create an Ollama model from a GGUF file.

    Args:
        gguf_path: Path to the GGUF model file.
        model_name: Name for the Ollama model.

    Returns:
        True if successful.
    """
    gguf_path = os.path.abspath(gguf_path)

    # Create Modelfile
    modelfile_content = MODELFILE_TEMPLATE.format(gguf_path=gguf_path)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".modelfile", delete=False, encoding="utf-8",
    ) as f:
        f.write(modelfile_content)
        modelfile_path = f.name

    try:
        result = subprocess.run(
            ["ollama", "create", model_name, "-f", modelfile_path],
            check=True,
            capture_output=True,
            text=True,
        )
        logger.info("Ollama model '%s' created successfully", model_name)
        return True

    except subprocess.CalledProcessError as e:
        logger.error("Failed to create Ollama model: %s\n%s", e, e.stderr)
        return False

    except FileNotFoundError:
        logger.error("Ollama not found. Install from https://ollama.com")
        return False

    finally:
        os.unlink(modelfile_path)


def export_full_pipeline(
    adapter_path: str,
    base_model: str = "Qwen/Qwen2.5-7B",
    output_dir: str = "models/finetuned",
    model_name: str = "transport-qwen",
    quantization: str = "q4_k_m",
) -> dict:
    """Run the full export pipeline: merge -> GGUF -> Ollama.

    Args:
        adapter_path: Path to the LoRA adapter directory.
        base_model: HuggingFace base model ID.
        output_dir: Directory for output files.
        model_name: Ollama model name.
        quantization: GGUF quantization type.

    Returns:
        Dict with paths and status.
    """
    os.makedirs(output_dir, exist_ok=True)

    # Step 1: Merge
    merged_path = os.path.join(output_dir, "merged")
    merge_lora(base_model, adapter_path, merged_path)

    # Step 2: Convert to GGUF
    gguf_path = os.path.join(output_dir, f"{model_name}.gguf")
    convert_to_gguf(merged_path, gguf_path, quantization)

    # Step 3: Create Ollama model
    ollama_ok = create_ollama_model(gguf_path, model_name)

    return {
        "merged_path": merged_path,
        "gguf_path": gguf_path,
        "ollama_model": model_name if ollama_ok else None,
        "success": ollama_ok,
    }
