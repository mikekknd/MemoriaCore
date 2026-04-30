# 環境假設：Python 3.12, Requests 庫可用。MiniMax API Key 存於 user_prefs.json。
import base64
import json
import os
import re
import uuid
from pathlib import Path

import requests

from core.system_logger import SystemLogger


GENERATE_IMAGE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "generate_image",
        "description": (
            "<tool_description>\n"
            "<function>依照使用者的文字描述生成圖片。</function>\n"
            "<trigger>當使用者明確要求畫圖、生成圖片、製作插圖、產生視覺素材、角色圖、場景圖時呼叫。</trigger>\n"
            "<forbidden>若使用者要求生成目前 AI 角色本人、你的自畫像、你的外觀、你的形象，必須改用 generate_self_portrait。</forbidden>\n"
            "<limitation>此工具只支援文字生圖，不支援上傳或分析使用者圖片。</limitation>\n"
            "</tool_description>"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "圖片生成提示詞。請完整描述使用者指定的主體、風格、構圖、色彩與細節。不可用於目前 AI 角色本人的自畫像或形象圖。",
                },
                "aspect_ratio": {
                    "type": "string",
                    "enum": ["1:1", "16:9", "9:16", "4:3", "3:4"],
                    "description": "圖片長寬比。未指定時使用 1:1。",
                },
            },
            "required": ["prompt"],
        },
    },
}


GENERATE_SELF_PORTRAIT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "generate_self_portrait",
        "description": (
            "<tool_description>\n"
            "<function>生成目前 AI 角色本人的圖片。</function>\n"
            "<trigger>當使用者要求你的自畫像、你的外觀、你的形象、你本人、目前角色的圖片、角色自拍或立繪時呼叫。</trigger>\n"
            "<forbidden>不可用於其他人物、物品、一般場景或使用者未指涉目前 AI 角色本人的圖片。</forbidden>\n"
            "</tool_description>"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "使用者額外指定的姿勢、表情、構圖、場景、服裝變化或畫風。不要重複或編造角色固有外觀，後端會自動加入角色 visual_prompt。",
                },
                "aspect_ratio": {
                    "type": "string",
                    "enum": ["1:1", "16:9", "9:16", "4:3", "3:4"],
                    "description": "圖片長寬比。未指定時使用 1:1。",
                },
            },
            "required": ["prompt"],
        },
    },
}


_ASPECT_RATIOS = {"1:1", "16:9", "9:16", "4:3", "3:4"}
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_GENERATED_ROOT = _PROJECT_ROOT / "generated_images"


def _get_minimax_key() -> str:
    try:
        from core.storage_manager import StorageManager

        prefs = StorageManager().load_prefs()
        key = prefs.get("minimax_api_key", "")
        if key:
            return str(key).strip()
    except Exception:
        pass
    return os.environ.get("MINIMAX_API_KEY", "").strip()


def _safe_segment(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", str(value or "").strip())
    return cleaned or fallback


def _image_url(session_id: str, image_id: str) -> str:
    return f"/api/v1/chat/generated-images/{session_id}/{image_id}.jpeg"


def generated_image_path(user_id: str, session_id: str, image_id: str) -> Path:
    safe_user = _safe_segment(user_id, "default")
    safe_session = _safe_segment(session_id, "session")
    safe_image = _safe_segment(image_id.removesuffix(".jpeg"), "image")
    return _GENERATED_ROOT / safe_user / safe_session / f"{safe_image}.jpeg"


def enrich_self_portrait_prompt(prompt: str, runtime_context: dict | None = None) -> str:
    """將目前角色外觀提示注入自畫像工具 prompt。"""
    prompt = (prompt or "").strip()
    ctx = runtime_context or {}
    visual_prompt = str(ctx.get("visual_prompt") or "").strip()
    if not visual_prompt:
        return prompt
    return (
        f"{visual_prompt}\n\n"
        f"User image request:\n{prompt}\n\n"
        "Generate the current assistant character described above, not a generic AI, robot, or unrelated person."
    ).strip()


def generate_image(
    prompt: str,
    aspect_ratio: str = "1:1",
    runtime_context: dict | None = None,
    source_prompt: str | None = None,
) -> str:
    """呼叫 MiniMax Image Generation 並將圖片保存為受驗證 API 可讀的 JPEG。"""
    prompt = (prompt or "").strip()
    if not prompt:
        return json.dumps({"error": "圖片生成失敗：prompt 不可為空。"}, ensure_ascii=False)

    if aspect_ratio not in _ASPECT_RATIOS:
        aspect_ratio = "1:1"

    api_key = _get_minimax_key()
    if not api_key:
        return json.dumps({"error": "系統尚未設定 MiniMax API Key，請前往設定介面填寫後再試。"}, ensure_ascii=False)

    ctx = runtime_context or {}
    user_id = _safe_segment(ctx.get("user_id", "default"), "default")
    session_id = _safe_segment(ctx.get("session_id", "session"), "session")
    image_id = uuid.uuid4().hex
    final_prompt = prompt

    payload = {
        "model": "image-01",
        "prompt": final_prompt,
        "aspect_ratio": aspect_ratio,
        "response_format": "base64",
    }
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        resp = requests.post(
            "https://api.minimax.io/v1/image_generation",
            headers=headers,
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        images = data.get("data", {}).get("image_base64", [])
        if not images:
            return json.dumps({"error": "MiniMax 未回傳圖片資料。"}, ensure_ascii=False)

        image_bytes = base64.b64decode(images[0])
        path = generated_image_path(user_id, session_id, image_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image_bytes)

        url = _image_url(session_id, image_id)
        markdown = f"![generated image]({url})"
        return json.dumps(
            {
                "generated_images": [
                    {
                        "url": url,
                        "markdown": markdown,
                        "prompt": source_prompt or prompt,
                        "final_prompt": final_prompt,
                        "aspect_ratio": aspect_ratio,
                    }
                ]
            },
            ensure_ascii=False,
        )
    except Exception as e:
        SystemLogger.log_error("MiniMaxImage", f"{type(e).__name__}: {e}")
        return json.dumps({"error": f"圖片生成過程中發生錯誤: {e}"}, ensure_ascii=False)


def generate_self_portrait(
    prompt: str,
    aspect_ratio: str = "1:1",
    runtime_context: dict | None = None,
) -> str:
    """生成目前 AI 角色本人圖片，永遠先注入 active character 的 visual_prompt。"""
    enriched = enrich_self_portrait_prompt(prompt, runtime_context)
    return generate_image(enriched, aspect_ratio, runtime_context=runtime_context, source_prompt=prompt)


def collect_generated_image_markdown(tool_results: list[dict] | None) -> list[str]:
    """從工具結果中收集圖片 Markdown，供對話回覆保底附加。"""
    markdowns: list[str] = []
    for item in tool_results or []:
        raw = item.get("result", "")
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            continue
        for img in payload.get("generated_images", []) if isinstance(payload, dict) else []:
            md = img.get("markdown")
            url = img.get("url")
            if md:
                markdowns.append(md)
            elif url:
                markdowns.append(f"![generated image]({url})")
    return markdowns


def append_generated_images(reply_text: str, tool_results: list[dict] | None) -> str:
    """若模型沒有自行引用圖片 URL，將產圖結果附加到回覆尾端。"""
    output = reply_text or ""
    missing = []
    for markdown in collect_generated_image_markdown(tool_results):
        if markdown not in output:
            missing.append(markdown)
    if not missing:
        return output
    suffix = "\n\n" + "\n\n".join(missing)
    return output.rstrip() + suffix
