"""LLM backend abstraction.

Provides a unified interface for Ollama (local) and cloud APIs (OpenAI, Anthropic).
Each backend implements structured JSON output via the same `chat()` method.
"""
import json
import logging
from typing import Any, Dict, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class LLMBackend(Protocol):
    """Protocol for LLM backends — any backend must implement `chat()`."""

    def chat(
        self,
        messages: list[dict[str, str]],
        json_schema: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Send messages and return the assistant's text response.

        Args:
            messages: List of {"role": ..., "content": ...} dicts.
            json_schema: Optional JSON schema for structured output.

        Returns:
            The assistant's response text.
        """
        ...


class OllamaBackend:
    """Local Ollama backend — calls ollama.chat() with optional JSON schema."""

    def __init__(self, model: str = "qwen2.5:7b"):
        self.model = model
        self._verify_connection()

    def _verify_connection(self):
        try:
            import ollama
            models = ollama.list()
            available = [m.model for m in models.models]
            if self.model not in available:
                base_name = self.model.split(":")[0]
                found = any(m.startswith(base_name) for m in available)
                if not found:
                    logger.warning(
                        "Model '%s' not found in Ollama. Available: %s",
                        self.model, available,
                    )
        except Exception as e:
            logger.warning("Cannot connect to Ollama: %s", e)

    def chat(
        self,
        messages: list[dict[str, str]],
        json_schema: Optional[Dict[str, Any]] = None,
    ) -> str:
        import ollama

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        if json_schema is not None:
            kwargs["format"] = json_schema

        response = ollama.chat(**kwargs)
        return response.message.content


class CloudBackend:
    """Cloud LLM backend — supports OpenAI and Anthropic APIs.

    Set provider="openai" or provider="anthropic".
    API key is read from OPENAI_API_KEY / ANTHROPIC_API_KEY env vars.
    """

    def __init__(
        self,
        provider: str = "openai",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.provider = provider.lower()
        if self.provider == "openai":
            self.model = model or "gpt-4o-mini"
            self._init_openai(api_key)
        elif self.provider == "anthropic":
            self.model = model or "claude-sonnet-4-5-20250929"
            self._init_anthropic(api_key)
        else:
            raise ValueError(f"Unknown cloud provider: {provider}")

    def _init_openai(self, api_key: Optional[str]):
        import openai
        if api_key:
            self._client = openai.OpenAI(api_key=api_key)
        else:
            self._client = openai.OpenAI()  # reads OPENAI_API_KEY

    def _init_anthropic(self, api_key: Optional[str]):
        import anthropic
        if api_key:
            self._client = anthropic.Anthropic(api_key=api_key)
        else:
            self._client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY

    def chat(
        self,
        messages: list[dict[str, str]],
        json_schema: Optional[Dict[str, Any]] = None,
    ) -> str:
        if self.provider == "openai":
            return self._chat_openai(messages, json_schema)
        else:
            return self._chat_anthropic(messages, json_schema)

    def _chat_openai(
        self,
        messages: list[dict[str, str]],
        json_schema: Optional[Dict[str, Any]],
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        if json_schema is not None:
            kwargs["response_format"] = {"type": "json_object"}

        response = self._client.chat.completions.create(**kwargs)
        return response.choices[0].message.content

    def _chat_anthropic(
        self,
        messages: list[dict[str, str]],
        json_schema: Optional[Dict[str, Any]],
    ) -> str:
        # Anthropic uses system prompt separately
        system_text = ""
        chat_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_text = msg["content"]
            else:
                chat_messages.append(msg)

        if json_schema is not None and system_text:
            system_text += "\n\nRespond with valid JSON only."

        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 1024,
            "messages": chat_messages,
        }
        if system_text:
            kwargs["system"] = system_text

        response = self._client.messages.create(**kwargs)
        return response.content[0].text


def create_backend(
    backend_type: str = "ollama",
    model: Optional[str] = None,
    api_key: Optional[str] = None,
) -> LLMBackend:
    """Factory to create an LLM backend.

    Args:
        backend_type: "ollama", "openai", or "anthropic".
        model: Model name (default depends on backend).
        api_key: API key for cloud backends.

    Returns:
        An LLMBackend instance.
    """
    backend_type = backend_type.lower()

    if backend_type == "ollama":
        return OllamaBackend(model=model or "qwen2.5:7b")
    elif backend_type in ("openai", "anthropic"):
        return CloudBackend(provider=backend_type, model=model, api_key=api_key)
    else:
        raise ValueError(
            f"Unknown backend type: {backend_type}. "
            f"Choose from: ollama, openai, anthropic"
        )
