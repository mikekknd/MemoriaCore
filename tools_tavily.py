import os
import requests
import json
from system_logger import SystemLogger

def _get_tavily_key():
    try:
        from storage_manager import StorageManager
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
        "description": "搜尋網際網路以取得最新資訊、新聞或解答使用者的具體問題。當你需要了解最新時事、未知的實體或客觀知識時，請呼叫此函數。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要搜尋的關鍵字或問題描述"
                },
                "topic": {
                    "type": "string",
                    "enum": ["general", "news"],
                    "description": "搜尋主題：'general' 為一般搜尋，'news' 會鎖定最新新聞。"
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
        SystemLogger.log_error("Tavily Search Error: 尚未設定 TAVILY_API_KEY 環境變數或參數")
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
        SystemLogger.log_error(f"Tavily Search Exception: {e}")
        return json.dumps({"error": f"網路搜尋過程中發生錯誤: {e}"}, ensure_ascii=False)

def execute_tool_call(tool_call: dict) -> str:
    """
    接收 LLM 的 tool_call 結構並執行對應的工具
    tool_call 結構範例:
    {
        "function": {
            "name": "search_web",
            "arguments": {"query": "...", "topic": "news"}
        }
    }
    """
    func_name = tool_call.get("function", {}).get("name")
    args = tool_call.get("function", {}).get("arguments", {})
    
    if func_name == "search_web":
        query = args.get("query", "")
        topic = args.get("topic", "general")
        return search_web(query, topic)
    
    return json.dumps({"error": f"找不到工具 {func_name}"}, ensure_ascii=False)
