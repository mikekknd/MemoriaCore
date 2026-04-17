"""
LLM abstraction layer for PersonaProbe.
Supports Ollama (local) and OpenRouter (online) via OpenAI-compatible API.
"""

from dataclasses import dataclass, field
from typing import Iterator
import requests
from openai import OpenAI


@dataclass
class LLMConfig:
    provider: str           # "ollama" | "openrouter"
    model: str
    api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"
    temperature: float = 0.7
    max_tokens: int = 8192


class LLMClient:
    def __init__(self, config: LLMConfig):
        self.config = config
        if config.provider == "ollama":
            self._client = OpenAI(
                base_url=f"{config.ollama_base_url}/v1",
                api_key="ollama",
            )
        elif config.provider == "openrouter":
            self._client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=config.api_key,
            )
        else:
            raise ValueError(f"Unknown provider: {config.provider}")

    def chat(
        self,
        messages: list[dict],
        stream: bool = False,
        temperature: float = None,
        max_tokens: int = None,
    ) -> str | Iterator:
        temp = temperature if temperature is not None else self.config.temperature
        tok = max_tokens if max_tokens is not None else self.config.max_tokens
        response = self._client.chat.completions.create(
            model=self.config.model,
            messages=messages,
            temperature=temp,
            max_tokens=tok,
            stream=stream,
        )
        if stream:
            return _stream_generator(response)
        return response.choices[0].message.content


def _stream_generator(response) -> Iterator[str]:
    for chunk in response:
        delta = chunk.choices[0].delta
        if delta and delta.content:
            yield delta.content


def list_ollama_models(base_url: str = "http://localhost:11434") -> list[str]:
    """Returns list of locally available Ollama model names, or [] on failure."""
    try:
        resp = requests.get(f"{base_url}/api/tags", timeout=3)
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]
    except Exception:
        return []


def check_ollama_health(base_url: str = "http://localhost:11434") -> bool:
    try:
        resp = requests.get(base_url, timeout=2)
        return resp.status_code == 200
    except Exception:
        return False


def fetch_openrouter_models(api_key: str) -> list[str]:
    """Fetches available OpenRouter model IDs. Cache this in the caller."""
    try:
        resp = requests.get(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        resp.raise_for_status()
        models = [m["id"] for m in resp.json().get("data", [])]
        return sorted(models)
    except Exception:
        return []
