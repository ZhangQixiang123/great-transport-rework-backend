"""LLM backend abstraction — supports Ollama, OpenAI, and Anthropic."""

from .backend import CloudBackend, LLMBackend, OllamaBackend, create_backend
