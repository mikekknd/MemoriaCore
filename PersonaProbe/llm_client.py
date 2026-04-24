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
        response_format: dict | None = None,
    ) -> str | Iterator:
        """呼叫 chat completion。

        若傳入 ``response_format`` (JSON Schema dict)，會依 provider 轉換：
        - ``ollama``：經 OpenAI 相容端點時透過 ``extra_body={"format": schema}``
          對應 Ollama 原生 ``format`` 參數。
        - ``openrouter``：包成 OpenAI 的 ``response_format.json_schema``。

        ``strict=False`` 是故意的：避免某些代理模型因 strict schema 直接拒絕，
        若需嚴格模式可在呼叫端另行指定。
        """
        temp = temperature if temperature is not None else self.config.temperature
        tok = max_tokens if max_tokens is not None else self.config.max_tokens
        kwargs: dict = {
            "model": self.config.model,
            "messages": messages,
            "temperature": temp,
            "max_tokens": tok,
            "stream": stream,
        }
        if response_format is not None:
            if self.config.provider == "ollama":
                kwargs["extra_body"] = {"format": response_format}
            else:
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "dynamic_schema",
                        "schema": response_format,
                        "strict": False,
                    },
                }
        response = self._client.chat.completions.create(**kwargs)
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
