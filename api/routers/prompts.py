"""Prompt 管理 REST 端點 — 列出、編輯、重置 LLM Prompt 模板"""
from fastapi import APIRouter, Body
from core.prompt_manager import get_prompt_manager

router = APIRouter(prefix="/prompts", tags=["prompts"])


@router.get("")
async def list_prompts():
    """列出所有 prompt 的 key 與 metadata"""
    pm = get_prompt_manager()
    return [{"key": k, **pm.get_meta(k)} for k in pm.list_keys()]


@router.post("/reset-all")
async def reset_all_prompts():
    """重置所有 prompt 為內建預設（刪除 prompts.json）"""
    pm = get_prompt_manager()
    pm.reset_all()
    return {"status": "ok", "message": "所有 prompt 已重置為內建預設"}


@router.get("/{key}")
async def get_prompt(key: str):
    """取得單一 prompt 的完整 metadata"""
    pm = get_prompt_manager()
    return {"key": key, **pm.get_meta(key)}


@router.put("/{key}")
async def update_prompt(key: str, body: dict = Body(...)):
    """更新指定 prompt 的 template"""
    template = body.get("template")
    if template is None:
        raise ValueError("缺少 'template' 欄位")
    pm = get_prompt_manager()
    pm.update(key, template)
    return {"status": "ok", "key": key, **pm.get_meta(key)}


@router.post("/{key}/reset")
async def reset_prompt(key: str):
    """重置單一 prompt 為內建預設"""
    pm = get_prompt_manager()
    default_template = pm.reset_one(key)
    return {"status": "ok", "key": key, "default_template": default_template}
