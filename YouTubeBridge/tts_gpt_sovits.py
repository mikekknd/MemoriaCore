"""GPT-SoVITS TTS provider adapter for YouTubeBridge presentation output."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests


@dataclass(frozen=True)
class TTSResult:
    ok: bool
    audio_bytes: bytes = b""
    audio_format: str = "wav"
    error: str = ""


class GptSoVitsTTSProvider:
    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:9880",
        timeout: float = 60.0,
        session: requests.Session | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()

    def synthesize(self, text: str, profile: dict[str, Any]) -> TTSResult:
        text = str(text or "").strip()
        if not text:
            return TTSResult(ok=False, error="text is empty")
        ref_audio_path = str(profile.get("ref_audio_path") or "").strip()
        if not ref_audio_path:
            return TTSResult(ok=False, error="ref_audio_path is required")
        media_type = str(profile.get("media_type") or "wav").lower()
        payload = {
            "text": text,
            "text_lang": str(profile.get("text_lang") or "zh").lower(),
            "ref_audio_path": ref_audio_path,
            "aux_ref_audio_paths": profile.get("aux_ref_audio_paths") or [],
            "prompt_text": str(profile.get("prompt_text") or ""),
            "prompt_lang": str(profile.get("prompt_lang") or "zh").lower(),
            "text_split_method": str(profile.get("text_split_method") or "cut5"),
            "batch_size": int(profile.get("batch_size", 1) or 1),
            "speed_factor": float(profile.get("speed_factor", 1.0) or 1.0),
            "media_type": media_type,
            "streaming_mode": False,
        }
        try:
            response = self.session.post(
                f"{self.base_url}/tts",
                json=payload,
                timeout=self.timeout,
            )
        except Exception as exc:
            return TTSResult(ok=False, audio_format=media_type, error=str(exc)[:500])
        if response.status_code >= 400:
            return TTSResult(
                ok=False,
                audio_format=media_type,
                error=f"HTTP {response.status_code}: {response.text[:300]}",
            )
        if not response.content:
            return TTSResult(ok=False, audio_format=media_type, error="empty audio response")
        return TTSResult(ok=True, audio_bytes=response.content, audio_format=media_type)
