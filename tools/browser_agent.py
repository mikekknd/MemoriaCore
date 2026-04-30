"""Browser Subagent — 對外為普通 tool，內部持有 LLM loop。
CLI 工具：agent-browser（需安裝並加入 PATH）
"""
import json
import shlex
import shutil
import subprocess
from pathlib import Path
from core.system_logger import SystemLogger

# Windows 上 npm global 工具為 .cmd 檔，需透過 shutil.which 解析完整路徑
_AGENT_BROWSER_BIN: str | None = shutil.which("agent-browser")

_CORE_MD_PATH = Path(__file__).parent / "core.md"

# ── 對外 Schema（Router Agent 看到的） ──────────────────────────
BROWSER_AGENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "browser_task",
        "description": (
            "<tool_description>\n"
            "<function>控制本機瀏覽器執行網頁自動化任務，包含導航、表單填寫、按鈕點擊、截圖、資料擷取等操作。</function>\n"
            "<trigger>需要開啟瀏覽器操作網頁時呼叫，例如：開啟網址、自動填表、抓取動態網頁、登入網站、點擊頁面元素。</trigger>\n"
            "<not_applicable>本機檔案操作、執行腳本、系統指令等，請改用 run_bash。</not_applicable>\n"
            "</tool_description>"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "描述需要完成的瀏覽器任務，用自然語言清楚說明目標與步驟要求。",
                }
            },
            "required": ["task"],
        },
    },
}

# ── 內部 Schema（loop 內 LLM 使用，模組私有） ───────────────────
_AGENT_BROWSER_CMD_SCHEMA = {
    "type": "function",
    "function": {
        "name": "agent_browser_cmd",
        "description": (
            "執行一個 agent-browser CLI 指令步驟。任務完成後直接輸出結果文字，不要再呼叫此工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "args": {
                    "type": "string",
                    "description": (
                        "agent-browser 指令後面的完整參數字串，例如：\n"
                        "  open https://example.com\n"
                        "  snapshot -i\n"
                        "  click @e3\n"
                        "  fill @e4 \"user@example.com\"\n"
                        "  press Enter\n"
                        "  scroll down 500\n"
                        "  screenshot page.png\n"
                        "  close"
                    ),
                }
            },
            "required": ["args"],
        },
    },
}

_MAX_STEPS = 15
_STEP_TIMEOUT = 60        # 一般指令 timeout（秒）
_OPEN_TIMEOUT = 120       # open 指令 timeout（秒，重頁面可能需要較長時間）
_MAX_EMPTY_RETRIES = 2    # tool_calls 為空時最多重試次數


def _get_browser_system_prompt() -> str:
    try:
        from core.prompt_manager import get_prompt_manager
        base = get_prompt_manager().get("browser_agent_system")
    except Exception:
        base = (
            "你是一個瀏覽器自動化代理。逐步使用 agent_browser_cmd 工具完成任務。\n"
            "每一步先觀察頁面快照，再決定下一個操作。完成後輸出結果摘要，不要再呼叫工具。"
        )

    if _CORE_MD_PATH.exists():
        core_ref = _CORE_MD_PATH.read_text(encoding="utf-8")
        return f"{base}\n\n---\n\n# agent-browser CLI 完整參考\n\n{core_ref}"
    SystemLogger.log_system_event(
        "BrowserAgent",
        f"找不到 {_CORE_MD_PATH}，system prompt 將不含 CLI 參考文件，建議將 core.md 複製到 tools/ 目錄。",
    )
    return base


def _exec_agent_browser(args: str) -> str:
    """執行 agent-browser CLI，回傳 JSON 字串結果。"""
    if not _AGENT_BROWSER_BIN:
        return json.dumps(
            {"error": "找不到 agent-browser 指令，請確認已安裝（npm i -g agent-browser）並加入 PATH。"},
            ensure_ascii=False,
        )

    # ── eval --stdin 特殊處理 ──────────────────────────────────────
    # LLM 有時會用 "eval --stdin <JS code>" 格式，但 --stdin 需要透過 pipe 傳入程式碼。
    # 偵測此模式並自動將 <JS code> 轉成 stdin input。
    stdin_input: str | None = None
    stdin_marker = "--stdin"
    stripped = args.strip()
    if stripped.startswith("eval") and stdin_marker in stripped:
        marker_pos = stripped.index(stdin_marker)
        js_code = stripped[marker_pos + len(stdin_marker):].strip()
        if js_code:
            # 有額外的 JS 程式碼：轉成 stdin
            parts = ["eval", "--stdin"]
            stdin_input = js_code
            SystemLogger.log_system_event("BrowserAgent", f"eval --stdin 模式：JS code via stdin ({len(js_code)} chars)")
        else:
            # 只有 "eval --stdin"，沒有跟隨程式碼，正常解析
            try:
                parts = shlex.split(stripped, posix=False)
            except ValueError as e:
                return f"[參數解析失敗] {e}"
    else:
        try:
            parts = shlex.split(args, posix=False)
        except ValueError as e:
            return f"[參數解析失敗] {e}"

    # open / wait 給較長的 timeout，其他指令用一般 timeout
    first_arg = parts[0].lower() if parts else ""
    timeout = _OPEN_TIMEOUT if first_arg in ("open", "wait") else _STEP_TIMEOUT

    try:
        result = subprocess.run(
            [_AGENT_BROWSER_BIN] + parts,
            input=stdin_input,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        output = result.stdout or result.stderr or "（無輸出）"
        if len(output) > 4000:
            output = output[:4000] + "\n…（輸出過長，已截斷）"
        # 成功：回傳純文字（讓 LLM 直接讀取，不包 JSON wrapper）
        if result.returncode == 0:
            return output
        # 失敗：加上 exit code 提示
        return f"[exit code {result.returncode}]\n{output}"
    except subprocess.TimeoutExpired:
        return f"[逾時錯誤] 指令執行超過 {timeout} 秒，請嘗試改用 wait --load domcontentloaded 或直接執行 snapshot -i"
    except Exception as e:
        SystemLogger.log_error("BrowserAgent", f"subprocess 錯誤: {e}")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def _make_error_hint(args_str: str, result_str: str) -> str | None:
    """
    分析指令執行結果，若偵測到已知錯誤模式，回傳給 LLM 的診斷提示。
    回傳 None 代表沒有偵測到問題。
    """
    # 逾時錯誤（新格式純文字）
    if result_str.startswith("[逾時錯誤]"):
        return (
            "上一步執行逾時。建議：\n"
            "1. 改用 wait --load domcontentloaded（比 networkidle 寬鬆）\n"
            "2. 或直接執行 snapshot -i，頁面可能已部分載入"
        )

    # 非零 exit code
    if result_str.startswith("[exit code"):
        return (
            "上一步執行失敗。輸出內容已附上，請根據錯誤訊息決定下一步，或嘗試替代方法。"
        )

    # 已知錯誤關鍵字（在純文字輸出中搜尋）
    error_patterns = {
        "Ref not found": "Ref 已失效（頁面可能已變動）。請重新執行 snapshot -i 取得新的 @eN ref。",
        "Element not found": "找不到元素。請重新 snapshot -i 確認 ref，或改用 find text / find role 語意定位。",
        "net::ERR": "網路錯誤，頁面可能無法載入。請確認 URL 是否正確，或嘗試 wait --load domcontentloaded 後再 snapshot -i。",
        "CAPTCHA": "頁面出現 CAPTCHA 驗證，自動化操作可能被阻擋。建議嘗試截圖確認頁面狀態。",
        "ERR_CONNECTION": "連線失敗，請確認網路狀態與目標網址。",
    }
    for keyword, hint in error_patterns.items():
        if keyword.lower() in result_str.lower():
            return hint

    return None


def run_browser_agent(task: str) -> str:
    """
    Browser Subagent 主入口。
    對外等同普通工具函式：接收任務字串，回傳 JSON 結果字串。
    """
    try:
        from api.dependencies import get_router
        router = get_router()
    except Exception as e:
        return json.dumps({"error": f"無法取得 LLMRouter: {e}"}, ensure_ascii=False)

    system_prompt = _get_browser_system_prompt()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]

    executed_steps = 0   # 已實際執行過的 CLI 步驟數
    empty_retries = 0    # 連續空回應重試次數

    for step in range(1, _MAX_STEPS + 1):
        # DEBUG：印出送給 LLM 的完整 messages（排除 system prompt 避免過長）
        debug_msgs = [
            f"[{m['role']}] " + (
                f"tool_calls={[tc['function']['name'] for tc in m.get('tool_calls', [])]}"
                if m.get('tool_calls') else
                f"tool_call_id={m.get('tool_call_id', '')}" if m['role'] == 'tool' else
                (m.get('content', '') or '')[:200]
            )
            for m in messages if m['role'] != 'system'
        ]
        SystemLogger.log_system_event("BrowserAgent", f"Step {step} messages:\n" + "\n".join(debug_msgs))
        SystemLogger.log_system_event("BrowserAgent", f"Step {step}: 等待 LLM 回應…")
        try:
            content, tool_calls = router.generate_with_tools(
                "browser",
                messages,
                tools=[_AGENT_BROWSER_CMD_SCHEMA],
                temperature=0.3,
            )
        except Exception as e:
            SystemLogger.log_error("BrowserAgent", f"LLM 呼叫失敗 (step {step}): {e}")
            return json.dumps({"error": f"LLM 呼叫失敗: {e}"}, ensure_ascii=False)
        SystemLogger.log_system_event("BrowserAgent", f"Step {step}: LLM 回應完畢")

        # LLM 未回傳 tool call
        if not tool_calls:
            # 尚未執行任何步驟且還有重試額度 → 補提示重試
            if executed_steps == 0 and empty_retries < _MAX_EMPTY_RETRIES:
                empty_retries += 1
                SystemLogger.log_system_event(
                    "BrowserAgent",
                    f"Step {step}: LLM 未回傳 tool call（尚未執行任何操作），"
                    f"補提示重試 ({empty_retries}/{_MAX_EMPTY_RETRIES})",
                )
                messages.append({"role": "assistant", "content": content or ""})
                messages.append({
                    "role": "user",
                    "content": "你必須呼叫 agent_browser_cmd 工具來執行瀏覽器操作，請立即開始第一步。",
                })
                continue

            # 已執行過步驟，或重試耗盡 → loop 結束
            SystemLogger.log_system_event(
                "BrowserAgent",
                f"Step {step}: LLM 未回傳 tool call，loop 結束。"
                f" content preview: {(content or '')[:200]}",
            )
            # content 空白代表 LLM 沒有輸出摘要，補一輪強制要求
            if not content or not content.strip():
                SystemLogger.log_system_event("BrowserAgent", "content 為空，補送摘要請求")
                messages.append({"role": "assistant", "content": ""})
                messages.append({"role": "user", "content": "請根據以上操作結果，輸出繁體中文的任務結果摘要。"})
                try:
                    content, _ = router.generate_with_tools(
                        "browser", messages, tools=[], temperature=0.3
                    )
                except Exception:
                    content = "（摘要產生失敗）"
            return json.dumps({"result": content or "（無回覆）"}, ensure_ascii=False)

        empty_retries = 0  # 有 tool call → 重置重試計數
        SystemLogger.log_system_event(
            "BrowserAgent",
            f"Step {step}: LLM 回傳 {len(tool_calls)} 個 tool call(s)",
        )

        # 將 assistant 的決策加進 messages
        messages.append({
            "role": "assistant",
            "content": content or "",
            "tool_calls": tool_calls,
        })

        for tc in tool_calls:
            func_name = tc.get("function", {}).get("name", "")
            args_dict = tc.get("function", {}).get("arguments", {})
            tc_id = tc.get("id", f"call_{step}")

            if func_name != "agent_browser_cmd":
                result_str = json.dumps(
                    {"error": f"未知工具名稱: {func_name}"},
                    ensure_ascii=False,
                )
                SystemLogger.log_system_event("BrowserAgent", f"Step {step}: 未知工具 {func_name}")
            else:
                args_str = args_dict.get("args", "") if isinstance(args_dict, dict) else str(args_dict)
                SystemLogger.log_system_event("BrowserAgent", f"Step {step}: agent-browser {args_str}")
                result_str = _exec_agent_browser(args_str)
                executed_steps += 1
                SystemLogger.log_system_event("BrowserAgent", f"Step {step} result: {result_str[:300]}")

            messages.append({
                "role": "tool",
                "content": result_str,
                "tool_call_id": tc_id,
            })

            # 錯誤偵測：注入診斷提示，讓 LLM 知道該怎麼處理
            if func_name == "agent_browser_cmd":
                hint = _make_error_hint(args_str, result_str)
                if hint:
                    SystemLogger.log_system_event("BrowserAgent", f"Step {step}: 偵測到錯誤，注入提示：{hint[:100]}")
                    messages.append({
                        "role": "user",
                        "content": (
                            "<browser_diagnostic_hint>\n"
                            f"{hint}\n"
                            "</browser_diagnostic_hint>"
                        ),
                    })

    return json.dumps(
        {"error": f"Browser Agent 已達到最大步驟數（{_MAX_STEPS}），任務未完成。"},
        ensure_ascii=False,
    )
