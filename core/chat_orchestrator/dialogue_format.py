"""對話訊息清洗與格式化單一入口。

供以下模組共用：
- 最終 LLM chat 上下文（由 coordinator / orchestration 呼叫 format_history_for_llm）
- 記憶分析 prompts（由 memory_analyzer / core_memory 呼叫 format_dialogue_for_analysis）
- Citation metadata 讀取（由 coordinator / orchestration 呼叫 collect_cited_uids）

所有清洗規則集中於此，保證群組標籤與內部標記的處理在所有 LLM 進入點一致。
"""
import re


# ════════════════════════════════════════════════════════════
# SECTION: 內部標記偵測 regex
# ════════════════════════════════════════════════════════════

_REF_TAG = re.compile(r'\s*\[Ref:\s*[^\]]+\]\s*')
_ENV_BLOCK = re.compile(r'<environment_context>.*?</environment_context>\s*', re.DOTALL)
_EMO_BLOCK = re.compile(r'<emotional_trajectory>.*?</emotional_trajectory>\s*', re.DOTALL)
_FOLLOWUP_HEADER = '【群組接力指令】'
_FOLLOWUP_XML = '<group_followup_instruction>'


# ════════════════════════════════════════════════════════════
# SECTION: 訊息清洗
# ════════════════════════════════════════════════════════════

def sanitize_message_for_llm(content: str) -> str:
    """移除內部標記，回傳乾淨的對話正文。

    清洗範圍：
    - `[Ref: uid1, uid2]` 引用標記
    - `<environment_context>...</environment_context>` 環境上下文區塊
    - `<emotional_trajectory>...</emotional_trajectory>` 情緒軌跡區塊
    - 整則群組接力指令訊息回傳空字串（呼叫端應跳過）

    舊資料中 [Ref:] 仍可能存在於 DB；本函式即時清洗，不需資料遷移。
    """
    if not content:
        return ""
    # 接力指令訊息整則丟棄
    stripped = content.lstrip()
    if stripped.startswith(_FOLLOWUP_HEADER) or stripped.startswith(_FOLLOWUP_XML):
        return ""
    cleaned = _REF_TAG.sub(' ', content)
    cleaned = _ENV_BLOCK.sub('', cleaned)
    cleaned = _EMO_BLOCK.sub('', cleaned)
    return cleaned.strip()


# ════════════════════════════════════════════════════════════
# SECTION: Citation metadata 讀取
# ════════════════════════════════════════════════════════════

def collect_cited_uids(message: dict) -> list[str]:
    """從訊息提取 cited memory UIDs。

    主路徑：讀 message['debug_info']['cited_uids']（新版寫入路徑）。
    Fallback：從 content 用 regex 抽 [Ref: uid] 以相容舊資料。
    """
    debug_info = message.get("debug_info") or {}
    if isinstance(debug_info, dict):
        uids = debug_info.get("cited_uids")
        if isinstance(uids, list) and uids:
            return [str(u) for u in uids if u]
    # Fallback：舊資料
    content = message.get("content", "") or ""
    matches = re.findall(r'\[Ref:\s*([^\]]+)\]', content)
    out: list[str] = []
    for m in matches:
        for uid in m.split(","):
            uid = uid.strip()
            if uid:
                out.append(uid)
    return out


# ════════════════════════════════════════════════════════════
# SECTION: 群組標籤工具
# ════════════════════════════════════════════════════════════

def speaker_label(message: dict) -> str:
    """回傳 'name|character_id'，缺值時退回單側，全缺則空字串。"""
    name = str(message.get("character_name") or "").strip()
    character_id = str(message.get("character_id") or "").strip()
    if name and character_id:
        return f"{name}|{character_id}"
    return name or character_id


def _is_group_session(messages: list[dict], force_group: bool = False) -> bool:
    """≥2 個不同的 character_id 視為群組 session。"""
    if force_group:
        return True
    seen = set()
    for m in messages:
        if m.get("role") != "assistant":
            continue
        cid = m.get("character_id")
        if cid:
            seen.add(cid)
    return len(seen) > 1


# ════════════════════════════════════════════════════════════
# SECTION: 最終 LLM 上下文（chat 對話）
# ════════════════════════════════════════════════════════════

def format_history_for_llm(messages: list[dict], force_group: bool = False) -> list[dict]:
    """組裝供最終對話 LLM 看的訊息列表：
    - 群組 session 的 assistant 訊息加 `[name|character_id]: ` 前綴
    - 內部標記（[Ref:] / 環境 / 情緒 / 接力指令）被 sanitize 移除
    - 接力指令訊息（清洗後 content 為空）整則跳過
    回傳新 list，僅保留 role + 過濾後 content（不帶 character_id 等 metadata）。
    """
    is_group = _is_group_session(messages, force_group=force_group)
    formatted: list[dict] = []
    for m in messages:
        role = m.get("role", "")
        raw_content = m.get("content", "")
        cleaned = sanitize_message_for_llm(raw_content)
        if not cleaned:
            # sanitize 完空字串：通常是接力指令訊息，跳過
            continue
        if is_group and role == "assistant":
            label = speaker_label(m)
            if label:
                cleaned = f"[{label}]: {cleaned}"
        formatted.append({"role": role, "content": cleaned})
    return formatted


# ════════════════════════════════════════════════════════════
# SECTION: 記憶分析 dialogue 文字（expand / pipeline / facts）
# ════════════════════════════════════════════════════════════

def format_dialogue_for_analysis(messages: list[dict], force_group: bool = False) -> str:
    """組裝供 LLM 分析任務（query_expand / memory_pipeline / extract_user_facts）
    使用的純文字 dialogue。

    - 每行一則訊息
    - 群組 session 的 assistant 訊息以 `[name|character_id]:` 為前綴
    - 單一 character session 仍用 `assistant:`
    - user 一律用 `user:`
    - 自動 sanitize 內部標記；接力指令訊息整則跳過
    """
    is_group = _is_group_session(messages, force_group=force_group)
    lines: list[str] = []
    for m in messages:
        role = m.get("role", "") or ""
        raw_content = m.get("content", "")
        cleaned = sanitize_message_for_llm(raw_content)
        if not cleaned:
            continue
        if role == "assistant" and is_group:
            label = speaker_label(m) or "assistant"
            lines.append(f"[{label}]: {cleaned}")
        else:
            lines.append(f"{role}: {cleaned}")
    return "\n".join(lines)


def snapshot_messages_for_pipeline(messages: list[dict]) -> list[dict]:
    """保留記憶管線需要的訊息欄位，避免群組角色 metadata 在背景管線遺失。"""
    out: list[dict] = []
    for m in messages:
        item = {"role": m.get("role", ""), "content": m.get("content", "")}
        if m.get("character_name"):
            item["character_name"] = m.get("character_name")
        if m.get("character_id"):
            item["character_id"] = m.get("character_id")
        out.append(item)
    return out
