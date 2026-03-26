# 【環境假設】：Python 3.12。獨立日誌模組，解耦 UI 與系統輸出。
# 同時輸出至終端機與結構化 JSON Lines 日誌檔。
import json
import os
from datetime import datetime

# 日誌檔路徑 (專案根目錄)
_LOG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG_FILE = os.path.join(_LOG_DIR, "llm_trace.jsonl")


class SystemLogger:

    # ------------------------------------------------------------------
    # 內部工具
    # ------------------------------------------------------------------
    @staticmethod
    def _get_time():
        return datetime.now().strftime("%H:%M:%S")

    @staticmethod
    def _get_iso_time():
        return datetime.now().isoformat()

    @staticmethod
    def _write_entry(entry: dict):
        """將一筆結構化紀錄以 JSON Lines 格式追加寫入日誌檔。"""
        try:
            with open(_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[Logger] 寫入日誌檔失敗: {e}")

    # ------------------------------------------------------------------
    # 公開 API — 每個方法同時印出終端訊息 + 寫入檔案
    # ------------------------------------------------------------------

    @staticmethod
    def log_system_event(category, message):
        """通用系統關鍵事件紀錄"""
        ts = SystemLogger._get_time()
        print(f"\n[{ts}] 系統事件 [{category}]")
        print(f"  -> {message}")
        print(f"{'-'*60}\n")

        SystemLogger._write_entry({
            "timestamp": SystemLogger._get_iso_time(),
            "type": "system_event",
            "category": category,
            "message": message,
        })

    @staticmethod
    def log_shift_trigger(cohesion_score, threshold, trigger_msg):
        ts = SystemLogger._get_time()
        if cohesion_score == -1.0:
            reason = "強制脈絡深度切斷 (防止 Token 溢出)"
        else:
            reason = f"凝聚度: {cohesion_score:.2f} (閾值: {threshold:.2f})"

        print(f"\n{'='*60}")
        print(f"[{ts}] 偵測到話題偏移 (Topic Shift)")
        print(f"  原因: {reason}")
        print(f"  觸發句: {trigger_msg}")
        print(f"{'='*60}\n")

        SystemLogger._write_entry({
            "timestamp": SystemLogger._get_iso_time(),
            "type": "system_event",
            "category": "話題偏移偵測",
            "message": reason,
            "details": {
                "cohesion_score": cohesion_score,
                "threshold": threshold,
                "trigger_message": trigger_msg,
            },
        })

    @staticmethod
    def log_pipeline_result(pipeline_res):
        ts = SystemLogger._get_time()
        print(f"\n[{ts}] 一體化管線處理完成")

        details = {}
        if "error" in pipeline_res:
            print(f"  錯誤: {pipeline_res['error']}")
            details["error"] = pipeline_res["error"]
        else:
            healed = pipeline_res.get("healed_entities")
            if healed:
                print(f"  成功修復歷史記憶: {healed}")
                details["healed_entities"] = healed
            else:
                print("  無需修復歷史記憶")

            new_mems = pipeline_res.get("new_memories", [])
            print(f"  生成新記憶區塊數量: {len(new_mems)}")
            details["new_memory_count"] = len(new_mems)
            mem_blocks = []
            for i, mem in enumerate(new_mems):
                ent = mem.get("entities", [])
                summary = mem.get("summary", "")
                print(f"    區塊 {i+1} 實體: {ent}")
                print(f"    區塊 {i+1} 摘要: {summary}")
                mem_blocks.append({"entities": ent, "summary": summary})
            details["new_memories"] = mem_blocks

        print(f"{'-'*60}\n")

        SystemLogger._write_entry({
            "timestamp": SystemLogger._get_iso_time(),
            "type": "system_event",
            "category": "記憶管線結果",
            "message": f"生成 {details.get('new_memory_count', 0)} 筆新記憶區塊",
            "details": details,
        })

    @staticmethod
    def log_error(context, error_msg):
        ts = SystemLogger._get_time()
        print(f"\n[{ts}] 錯誤 ({context}): {error_msg}\n")

        SystemLogger._write_entry({
            "timestamp": SystemLogger._get_iso_time(),
            "type": "error",
            "category": context,
            "message": error_msg,
        })

    @staticmethod
    def log_llm_prompt(task_key, model_name, api_messages):
        """紀錄發送給 LLM 的完整 Prompt"""
        ts = SystemLogger._get_time()
        print(f"\n[{ts}] >>> 發送至 LLM [任務: {task_key} | 模型: {model_name}]")
        print(f"{'-'*60}")
        for msg in api_messages:
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")
            print(f"  [{role}]:")
            # 終端機上只印前 500 字避免洗版
            preview = content[:500] + ("..." if len(content) > 500 else "")
            for line in preview.split("\n"):
                print(f"    {line}")
            print()
        print(f"{'-'*60}\n")

        SystemLogger._write_entry({
            "timestamp": SystemLogger._get_iso_time(),
            "type": "llm_call",
            "direction": "prompt",
            "category": task_key,
            "model": model_name,
            "messages": api_messages,
        })

    @staticmethod
    def log_llm_response(task_key, model_name, response_text):
        """紀錄 LLM 回傳的完整 Response"""
        ts = SystemLogger._get_time()
        print(f"\n[{ts}] <<< 接收自 LLM [任務: {task_key} | 模型: {model_name}]")
        print(f"{'-'*60}")
        preview = response_text[:800] + ("..." if len(response_text) > 800 else "")
        for line in preview.split("\n"):
            print(f"  {line}")
        print(f"\n{'-'*60}\n")

        SystemLogger._write_entry({
            "timestamp": SystemLogger._get_iso_time(),
            "type": "llm_call",
            "direction": "response",
            "category": task_key,
            "model": model_name,
            "content": response_text,
        })

    @staticmethod
    def log_profile_update(action, fact_key, fact_value, source=""):
        """使用者畫像提取事件"""
        emoji_map = {"INSERT": "新增", "UPDATE": "更新", "DELETE": "刪除"}
        action_label = emoji_map.get(action, action)
        ts = SystemLogger._get_time()
        print(f"\n[{ts}] 使用者畫像 [{action_label}]")
        print(f"  -> {fact_key} = {fact_value}")
        if source:
            print(f"  -> 來源: {source}")
        print(f"{'-'*60}\n")

        SystemLogger._write_entry({
            "timestamp": SystemLogger._get_iso_time(),
            "type": "system_event",
            "category": "使用者畫像更新",
            "message": f"[{action_label}] {fact_key} = {fact_value}",
            "details": {
                "action": action,
                "fact_key": fact_key,
                "fact_value": fact_value,
                "source": source,
            },
        })
