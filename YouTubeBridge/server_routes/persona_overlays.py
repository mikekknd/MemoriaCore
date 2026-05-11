"""直播專用角色 overlay routes。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from models import LivePersonaOverlayRequest, LiveTTSProfileRequest


router = APIRouter()
_state = None
storage = None
TTS_SOURCE_ROOT = Path(__file__).resolve().parents[2] / "runtime" / "YouTubeBridge" / "TTSSource"
TTS_AUDIO_EXTENSIONS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}
TTS_TRANSCRIPT_EXTENSION = ".txt"


def configure(state):
    global _state, storage
    _state = state
    storage = state.storage


@router.get("/persona-overlays")
async def list_persona_overlays():
    return {
        "overlays": storage.list_live_persona_overlays(),
        "tts_profiles": storage.list_tts_profiles(),
    }


@router.get("/persona-overlays/{character_id}")
async def get_persona_overlay(character_id: str):
    overlay = storage.get_live_persona_overlay(character_id)
    if not overlay:
        return {
            "character_id": character_id,
            "enabled": False,
            "mode": "replace",
            "system_prompt": "",
            "self_address": "",
            "addressing": {},
            "opening_intro": "",
            "reply_rules": "",
            "created_at": "",
            "updated_at": "",
        }
    return overlay


@router.post("/persona-overlays/{character_id}")
async def update_persona_overlay(character_id: str, body: LivePersonaOverlayRequest):
    try:
        return storage.upsert_live_persona_overlay(character_id, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _default_tts_profile(character_id: str) -> dict:
    return {
        "character_id": character_id,
        "ref_audio_path": "",
        "prompt_text": "",
        "text_lang": "zh",
        "prompt_lang": "zh",
        "speed_factor": 1.0,
        "media_type": "wav",
        "enabled": False,
        "created_at": "",
        "updated_at": "",
        "metadata": {},
    }


@router.get("/tts-sources")
async def list_tts_sources():
    sources = []
    root = TTS_SOURCE_ROOT
    if root.exists() and root.is_dir():
        for audio_path in sorted(root.rglob("*")):
            if not audio_path.is_file() or audio_path.suffix.lower() not in TTS_AUDIO_EXTENSIONS:
                continue
            transcript_path = audio_path.with_suffix(TTS_TRANSCRIPT_EXTENSION)
            if not transcript_path.is_file():
                continue
            try:
                prompt_text = transcript_path.read_text(encoding="utf-8").strip()
            except UnicodeDecodeError:
                prompt_text = transcript_path.read_text(encoding="utf-8", errors="replace").strip()
            name = audio_path.relative_to(root).with_suffix("").as_posix()
            sources.append({
                "name": name,
                "audio_path": str(audio_path),
                "transcript_path": str(transcript_path),
                "prompt_text": prompt_text,
            })
    return {
        "root": str(root),
        "sources": sources,
    }


@router.get("/persona-overlays/{character_id}/tts-profile")
async def get_tts_profile(character_id: str):
    return storage.get_tts_profile(character_id) or _default_tts_profile(character_id)


@router.post("/persona-overlays/{character_id}/tts-profile")
async def update_tts_profile(character_id: str, body: LiveTTSProfileRequest):
    try:
        data = body.model_dump()
        data["character_id"] = character_id
        return storage.upsert_tts_profile(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
