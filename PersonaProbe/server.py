"""
PersonaProbe Fragment Analysis API Server
供 MemoriaCore 等外部系統呼叫，執行非互動式片段分析。

啟動方式：
    python server.py
    # 或
    uvicorn server:app --host 0.0.0.0 --port 8089

端點：
    POST /analyze-fragments  →  回傳完整心智模型報告 + LLM 行為模板
"""

import json
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from llm_client import LLMClient, LLMConfig
from probe_engine import (
    _messages_to_text,
    build_fragment_aggregation_prompt,
    build_fragment_extraction_prompt,
    build_persona_md_prompt,
    load_fragments_from_db,
    parse_fragment_input_text,
    DIMENSION_SPECS,
)

app = FastAPI(
    title="PersonaProbe API",
    description="非互動式片段分析端點，供 MemoriaCore 呼叫以生成人格報告",
    version="1.0.0",
)

# ── 預設輸出根目錄（相對於 server.py 所在位置）─────────────────────────────────
_DEFAULT_OUTPUT_ROOT = Path(__file__).parent / "result"


# ── Request / Response Models ─────────────────────────────────────────────────

class FragmentAnalysisRequest(BaseModel):
    source: str = Field(
        "text",
        description='片段來源："text"（純文字）或 "db"（MemoriaCore conversation.db）',
    )
    # source == "text" 時使用
    text: str = Field(
        "",
        description='純文字對話片段，使用 user:/AI: 等前綴標記角色',
    )
    # source == "db" 時使用
    db_path: str = Field(
        "",
        description="conversation.db 的絕對路徑",
    )
    session_id: str = Field(
        "",
        description="指定 session ID（留空則載入全部 sessions）",
    )
    # 共用選填欄位
    existing_persona: str = Field(
        "",
        description="現有 Persona 文字，用於補全缺失維度（選填）",
    )
    output_dir: str = Field(
        "",
        description="輸出目錄根路徑（留空使用預設 result/ 目錄）",
    )
    # LLM 設定
    llm_provider: str = Field("ollama", description='"ollama" 或 "openrouter"')
    llm_model: str = Field("llama3", description="模型名稱或 ID")
    ollama_base_url: str = Field(
        "http://localhost:11434",
        description="Ollama 服務位址（provider==ollama 時使用）",
    )
    api_key: str = Field("", description="OpenRouter API Key（provider==openrouter 時使用）")
    temperature: float = Field(0.7, ge=0.0, le=2.0)


class FragmentAnalysisResponse(BaseModel):
    probe_report: str = Field(description="完整心智模型報告（Markdown）")
    persona: str = Field(description="LLM 行為模板（Markdown）")
    output_dir: str = Field(description="已儲存的輸出目錄路徑")
    dimensions_found: list[str] = Field(
        description="有足夠證據的維度名稱列表",
    )


# ── API Endpoint ──────────────────────────────────────────────────────────────

@app.post("/analyze-fragments", response_model=FragmentAnalysisResponse)
async def analyze_fragments(req: FragmentAnalysisRequest) -> FragmentAnalysisResponse:
    """
    從對話片段中提取 6 個人格維度，生成完整心智模型報告與 LLM 行為模板。

    流程：
    1. 解析片段（純文字或 DB 讀取）
    2. 對每個維度呼叫提取 prompt（共 6 次 LLM 呼叫）
    3. 聚合生成完整報告（1 次 LLM 呼叫）
    4. 萃取 persona.md（1 次 LLM 呼叫）
    5. 寫入輸出目錄並回傳結果
    """
    # ── 1. 解析片段 ──
    messages: list[dict] = []

    if req.source == "text":
        if not req.text.strip():
            raise HTTPException(status_code=422, detail="source=text 時 text 不能為空")
        messages = parse_fragment_input_text(req.text)
    elif req.source == "db":
        if not req.db_path:
            raise HTTPException(status_code=422, detail="source=db 時必須提供 db_path")
        try:
            messages = load_fragments_from_db(
                req.db_path,
                session_id=req.session_id or None,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"讀取資料庫失敗：{exc}") from exc
    else:
        raise HTTPException(status_code=422, detail=f"不支援的 source 類型：{req.source}")

    if not messages:
        raise HTTPException(status_code=422, detail="解析後的訊息列表為空，請確認輸入格式")

    fragments_text = _messages_to_text(messages)

    # ── 2. 建立 LLM Client ──
    try:
        config = LLMConfig(
            provider=req.llm_provider,
            model=req.llm_model,
            api_key=req.api_key,
            ollama_base_url=req.ollama_base_url,
            temperature=req.temperature,
        )
        client = LLMClient(config)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # ── 3. 6 維度提取 ──
    extraction_results: dict = {}

    for dim_id in sorted(DIMENSION_SPECS.keys()):
        prompt_msgs = build_fragment_extraction_prompt(
            dim_id, fragments_text, req.existing_persona
        )
        try:
            raw = client.chat(prompt_msgs)
            result = json.loads(raw)
        except json.JSONDecodeError:
            result = {"confidence": "none"}
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"維度 {dim_id} 提取失敗：{exc}",
            ) from exc
        extraction_results[dim_id] = result

    # ── 4. 聚合生成完整報告 ──
    try:
        agg_msgs = build_fragment_aggregation_prompt(extraction_results, fragments_text, req.existing_persona)
        full_report = client.chat(agg_msgs)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"報告生成失敗：{exc}") from exc

    # ── 5. 萃取 persona.md ──
    try:
        persona_msgs = build_persona_md_prompt(full_report, req.existing_persona)
        persona_content = client.chat(persona_msgs)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Persona 萃取失敗：{exc}") from exc

    # ── 6. 寫入輸出目錄 ──
    root = Path(req.output_dir) if req.output_dir else _DEFAULT_OUTPUT_ROOT
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = root / f"fragment-{timestamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "probe-report.md").write_text(full_report, encoding="utf-8")
    (out_dir / "persona.md").write_text(persona_content, encoding="utf-8")
    (out_dir / "fragment-input.md").write_text(
        f"# 原始輸入片段\n\n{fragments_text}", encoding="utf-8"
    )

    dimensions_found = [
        DIMENSION_SPECS[dim_id]["name"]
        for dim_id, result in extraction_results.items()
        if result.get("confidence", "none") != "none"
    ]

    return FragmentAnalysisResponse(
        probe_report=full_report,
        persona=persona_content,
        output_dir=str(out_dir),
        dimensions_found=dimensions_found,
    )


@app.get("/health")
async def health() -> dict:
    """服務健康檢查"""
    return {"status": "ok", "service": "PersonaProbe Fragment Analysis API"}


# ── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8089)
