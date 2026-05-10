"""管理用 LLM 任務端點。"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from api.dependencies import get_embed_model, get_memory_sys, get_router, require_admin_user
from api.models.requests import EmbedTextRequest, PromptJsonRequest
from core.prompt_manager import get_prompt_manager


router = APIRouter(prefix="/llm", tags=["llm"])


@router.post("/prompt-json")
async def generate_prompt_json(body: PromptJsonRequest, _current_user: dict = Depends(require_admin_user)):
    """以 PromptManager 模板執行結構化 LLM 任務。

    目前給 YouTubeBridge 摘要流程使用；保留 admin-only，避免外部任意觸發 LLM。
    """
    try:
        template = get_prompt_manager().get(body.prompt_key)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        prompt = template.format(**body.variables)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"缺少 prompt 變數：{exc}") from exc

    schema = body.response_schema or {"type": "object"}
    llm_router = get_router()
    result = await asyncio.to_thread(
        llm_router.generate_json,
        body.task_key,
        [{"role": "user", "content": prompt}],
        schema=schema,
        temperature=body.temperature,
        log_context={
            "source": "prompt_json",
            "prompt_key": body.prompt_key,
        },
    )
    if not result:
        raise HTTPException(status_code=502, detail="LLM 未回傳可解析 JSON")
    return {
        "prompt_key": body.prompt_key,
        "task_key": body.task_key,
        "result": result,
    }


@router.get("/prompt-template/{prompt_key}")
async def get_prompt_template(prompt_key: str, _current_user: dict = Depends(require_admin_user)):
    """回傳 prompt 模板字串，供外部子專案（如 YouTubeBridge）取用。"""
    try:
        return {"prompt_key": prompt_key, "template": get_prompt_manager().get(prompt_key)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/embed")
async def embed_text(body: EmbedTextRequest, _current_user: dict = Depends(require_admin_user)):
    """回傳目前 MemoriaCore embedding provider 的 dense vector。

    給 YouTubeBridge 的 Topic Pack 向量索引用；不寫入 memory_blocks。
    """
    memory_sys = get_memory_sys()
    if not memory_sys.embed_provider:
        raise HTTPException(status_code=503, detail="embedding provider not initialized")
    model = body.model.strip() or get_embed_model()
    result = memory_sys.embed_provider.get_embedding(text=body.text, model=model)
    dense = result.get("dense") if isinstance(result, dict) else None
    if not isinstance(dense, list) or not dense:
        raise HTTPException(status_code=502, detail="embedding provider returned empty vector")
    return {
        "model": model,
        "dense": dense,
        "dim": len(dense),
    }
