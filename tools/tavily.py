# 環境假設：Python 3.10+, Requests 庫, 已配置 StorageManager 與 SystemLogger
# 功能對齊：維持原有 Tavily API 呼叫邏輯，強化 query 參數的語言動態切換指令。

import os
import requests
import json
from core.system_logger import SystemLogger

def _get_tavily_key():
    try:
        from core.storage_manager import StorageManager
        prefs = StorageManager().load_prefs()
        key = prefs.get("tavily_api_key")
        if key: return key
    except Exception:
        pass
    return os.environ.get("TAVILY_API_KEY", "")

TAVILY_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_web",
        "description": "【功能】搜尋網際網路以取得客觀知識、最新資訊或時事解答。\n【觸發時機】當遇到知識盲區、需要查證事實，或使用者詢問真實世界存在的實體與事件時呼叫。\n【禁止事項】若使用者詢問天氣、氣溫、降雨機率等氣象數據，且 get_weather 工具可用，禁止呼叫本工具，應優先使用 get_weather。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜尋關鍵字。規則：\n1. 禁止輸入口語化句子，必須萃取出核心關鍵字。\n2. 專有名詞需加上引號（如 \"名探偵コナン\"）。\n3. 強制語言切換：根據主題切換至目標語言搜尋（例如：日本動漫/聲優用日文，歐美科技/開源專案用英文，台灣在地資訊用繁體中文）。"
                },
                "topic": {
                    "type": "string",
                    "enum": ["general", "news"],
                    "description": "搜尋範圍。絕大多數情況使用 'general'。只有在使用者明確詢問「最近幾天的新聞」、「即時財經快訊」時，才切換為 'news'。"
                }
            },
            "required": ["query"]
        }
    }
}

def search_web(query: str, topic: str = "general") -> str:
    """
    執行 Tavily 網路搜尋
    """
    api_key = _get_tavily_key()
    if not api_key:
        SystemLogger.log_error("Tavily", "尚未設定 TAVILY_API_KEY 環境變數或參數")
        return json.dumps({"error": "系統尚未設定 TAVILY_API_KEY，請前往設定介面填寫後再試。"}, ensure_ascii=False)
    
    url = "https://api.tavily.com/search"
    headers = {
        "Content-Type": "application/json"
    }
    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "basic",
        "topic": topic,
        "max_results": 3,
        "include_answer": False
    }
    
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        
        formatted_results = []
        for idx, r in enumerate(results, 1):
            title = r.get("title", "")
            content = r.get("content", "")
            formatted_results.append(f"[{idx}] {title}\n{content}")
        
        final_str = "\n\n".join(formatted_results)
        if not final_str:
            return json.dumps({"message": "找不到相關結果"}, ensure_ascii=False)
        
        return json.dumps({"search_results": final_str}, ensure_ascii=False)
        
    except Exception as e:
        SystemLogger.log_error("Tavily", f"搜尋過程中發生錯誤: {e}")
        return json.dumps({"error": f"網路搜尋過程中發生錯誤: {e}"}, ensure_ascii=False)

def execute_tool_call(tool_call: dict, runtime_context: dict | None = None) -> str:
    """
    統一工具調度中心 — 接收 LLM 的 tool_call 結構並執行對應的工具。
    新增工具時只需在此處加入對應的分支。
    """
    func_name = tool_call.get("function", {}).get("name")
    args = tool_call.get("function", {}).get("arguments", {})

    if func_name == "search_web":
        query = args.get("query", "")
        topic = args.get("topic", "general")
        return search_web(query, topic)

    if func_name == "get_weather":
        from tools.weather import get_weather
        city = args.get("city", "")
        mode = args.get("mode", "current")
        return get_weather(city, mode)

    if func_name == "run_bash":
        from tools.bash_tool import run_bash
        return run_bash(args.get("command", ""))

    if func_name == "browser_task":
        from tools.browser_agent import run_browser_agent
        return run_browser_agent(args.get("task", ""))

    if func_name == "generate_image":
        from tools.minimax_image import generate_image
        return generate_image(
            args.get("prompt", ""),
            args.get("aspect_ratio", "1:1"),
            runtime_context=runtime_context,
        )

    if func_name == "generate_self_portrait":
        from tools.minimax_image import generate_self_portrait
        return generate_self_portrait(
            args.get("prompt", ""),
            args.get("aspect_ratio", "1:1"),
            runtime_context=runtime_context,
        )

    return json.dumps({"error": f"找不到工具 {func_name}"}, ensure_ascii=False)
