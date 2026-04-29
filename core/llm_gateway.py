# 【環境假設】：Python 3.12, ollama, openai, numpy, onnxruntime, transformers 庫可用。
# 已在當前目錄透過 hf download 取得 BGE-M3 的 INT8 量化檔 (*.onnx)
import re
import ollama
from openai import OpenAI
import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer
import json
import glob
from core.system_logger import SystemLogger

_onnx_session = None
_tokenizer = None

def get_bge_m3_onnx_instance():
    global _onnx_session, _tokenizer
    if _onnx_session is None:
        onnx_files = glob.glob("StreamingAssets/Models/*.onnx")
        if not onnx_files:
            raise FileNotFoundError("[Error] 找不到任何 .onnx 檔案。")
        target_onnx = onnx_files[0]
        _onnx_session = ort.InferenceSession(target_onnx, providers=['CPUExecutionProvider'])
        _tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-m3")
    return _onnx_session, _tokenizer

class ILLMProvider:
    # 【核心修正】：新增 response_format 參數，並擴充 tools 與 tool_choice 以支援函數呼叫
    def generate_chat(self, messages: list, model: str, temperature: float = 0.0, response_format: dict | None = None, tools: list | None = None, tool_choice: str | dict = "auto") -> tuple[str, list]:
        raise NotImplementedError
        
    def get_embedding(self, text: str, model: str) -> dict:
        raise NotImplementedError

class OllamaProvider(ILLMProvider):
    def __init__(self, host: str = "http://localhost:11434"):
        self._client = ollama.Client(host=host)

    def _normalize_messages(self, messages: list) -> list:
        """Ollama 格式正規化：tool 訊息只保留 role + content，arguments 維持 dict"""
        normalized = []
        for msg in messages:
            if msg.get("role") == "tool":
                normalized.append({"role": "tool", "content": msg.get("content", "")})
            elif msg.get("role") == "assistant" and "tool_calls" in msg:
                tcs = []
                for tc in msg["tool_calls"]:
                    args = tc.get("function", {}).get("arguments", {})
                    if isinstance(args, str):
                        try: args = json.loads(args)
                        except Exception: args = {}
                    tcs.append({"function": {"name": tc["function"]["name"], "arguments": args}})
                normalized.append({"role": "assistant", "content": msg.get("content", ""), "tool_calls": tcs})
            else:
                normalized.append(msg)
        return normalized

    def generate_chat(self, messages: list, model: str, temperature: float = 0.0, response_format: dict | None = None, tools: list | None = None, tool_choice: str | dict = "auto") -> tuple[str, list]:
        kwargs = {
            "model": model,
            "messages": self._normalize_messages(messages),
            "options": {"temperature": temperature}
        }
        # 【核心修正】：Ollama 原生支援直接將 Schema 字典傳入 format 參數
        if response_format:
            kwargs["format"] = response_format
        if tools:
            kwargs["tools"] = tools
            # Ollama 不支援 tool_choice 參數，由 system prompt 指示模型行為

        response = self._client.chat(**kwargs)
        
        if hasattr(response, "model_dump"):
            resp_dict = response.model_dump()
        elif hasattr(response, "dict"):
            resp_dict = response.dict()
        else:
            resp_dict = dict(response) if not isinstance(response, dict) else response
            
        msg_data = resp_dict.get('message', {})
        content = msg_data.get('content', '') or ''
        tool_calls = msg_data.get('tool_calls', []) or []
        
        # 確保內部全部都是 dict 且補足 API 相容欄位
        clean_tcs = []
        for tc in tool_calls:
            tc_dict = tc.model_dump() if hasattr(tc, "model_dump") else (tc if isinstance(tc, dict) else dict(tc))
            if "function" in tc_dict and isinstance(tc_dict["function"], dict):
                # 確保 arguments 是 dict (避免部分框架回傳字串)
                args = tc_dict["function"].get("arguments", {})
                if isinstance(args, str):
                    import json
                    try: args = json.loads(args)
                    except: args = {}
                tc_dict["function"]["arguments"] = args
                
                clean_tcs.append({
                    "id": tc_dict.get("id", f"call_{tc_dict['function'].get('name', 'unknown')}"),
                    "type": tc_dict.get("type", "function"),
                    "function": tc_dict["function"]
                })
                
        return content.strip(), clean_tcs

    def get_embedding(self, text: str, model: str) -> dict:
        clean_text = re.sub(r'[\x00-\x1F\x7F-\x9F]', '', text)
        clean_text = clean_text.replace('\n', ' ').replace('\r', '').strip()
        if not clean_text: clean_text = "none"
        
        if "bge-m3" in model.lower():
            session, tokenizer = get_bge_m3_onnx_instance()
            inputs = tokenizer(clean_text, padding="longest", truncation=True, max_length=8192, return_tensors="np")
            input_ids = inputs["input_ids"][0]
            ort_inputs = {
                "input_ids": inputs["input_ids"].astype(np.int64),
                "attention_mask": inputs["attention_mask"].astype(np.int64)
            }
            outputs = session.run(None, ort_inputs)
            dense_vec = [float(x) for x in outputs[0][0]]
            sparse_weights = outputs[1][0]
            
            sparse_dict = {}
            for token_id, weight in zip(input_ids, sparse_weights):
                if token_id in tokenizer.all_special_ids:
                    continue
                token_str = str(token_id)
                w = float(weight.item())
                if token_str not in sparse_dict or w > sparse_dict[token_str]:
                    sparse_dict[token_str] = w
            return {"dense": dense_vec, "sparse": sparse_dict}
        else:
            try:
                response = self._client.embeddings(model=model, prompt=clean_text)
                return {"dense": response['embedding'], "sparse": {}}
            except Exception:
                try:
                    fallback_text = clean_text[:50]
                    response = self._client.embeddings(model=model, prompt=fallback_text)
                    return {"dense": response['embedding'], "sparse": {}}
                except Exception:
                    return {"dense": [], "sparse": {}}

class OpenAICompatibleProvider(ILLMProvider):
    def __init__(self, api_key: str, base_url: str = None):
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def _normalize_messages(self, messages: list) -> list:
        """OpenAI 格式正規化：tool 訊息需 tool_call_id，arguments 必須是 JSON string"""
        normalized = []
        for msg in messages:
            if msg.get("role") == "tool":
                normalized.append({
                    "role": "tool",
                    "tool_call_id": msg.get("tool_call_id", "call_unknown"),
                    "content": msg.get("content", ""),
                })
            elif msg.get("role") == "assistant" and "tool_calls" in msg:
                tcs = []
                for tc in msg["tool_calls"]:
                    args = tc.get("function", {}).get("arguments", {})
                    if isinstance(args, dict):
                        args = json.dumps(args, ensure_ascii=False)
                    tcs.append({
                        "id": tc.get("id", f"call_{tc['function']['name']}"),
                        "type": "function",
                        "function": {"name": tc["function"]["name"], "arguments": args},
                    })
                normalized.append({"role": "assistant", "content": msg.get("content", ""), "tool_calls": tcs})
            else:
                normalized.append(msg)
        return normalized

    def generate_chat(self, messages: list, model: str, temperature: float = 0.0, response_format: dict | None = None, tools: list | None = None, tool_choice: str | dict = "auto") -> tuple[str, list]:
        kwargs = {
            "model": model,
            "messages": self._normalize_messages(messages),
            "temperature": temperature,
        }
        # 【核心修正】：OpenAI 格式自動轉譯包裝
        if response_format:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "dynamic_schema",
                    "schema": response_format,
                    "strict": False
                }
            }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice

        import time
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                response = self.client.chat.completions.create(**kwargs)
                break
            except Exception as e:
                err_str = str(e)
                # 部分模型（推理模型）不支援自訂 temperature，自動降級重試
                if "temperature" in err_str and "unsupported_value" in err_str:
                    if attempt < max_retries:
                        kwargs.pop("temperature", None)
                        continue
                    raise
                
                if attempt < max_retries and any(code in err_str for code in ("502", "503", "429", "504", "temporary error")):
                    SystemLogger.log_error("LLMRouter", f"[{model}] API 暫時錯誤 ({err_str[:50]})，等待 3 秒後重試 {attempt+1}/{max_retries}...")
                    time.sleep(3.0)
                    continue
                raise
        choice = response.choices[0]
        msg = choice.message
        finish_reason = getattr(choice, 'finish_reason', 'unknown')
        content = msg.content or ''

        # 記錄 finish_reason，協助診斷模型為何不繼續呼叫工具
        if tools and finish_reason not in ('tool_calls', 'stop'):
            SystemLogger.log_system_event(
                "LLMRouter", f"[{model}] finish_reason={finish_reason}（非預期值）"
            )
        elif tools and finish_reason == 'stop' and not content.strip():
            SystemLogger.log_system_event(
                "LLMRouter", f"[{model}] finish_reason=stop 且 content 為空，模型可能拒絕繼續"
            )

        tool_calls_out = []
        if getattr(msg, 'tool_calls', None):
            import json  # fallback if not imported
            for tc in msg.tool_calls:
                args = tc.function.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                tool_calls_out.append({
                    "id": getattr(tc, "id", f"call_{tc.function.name}"),
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": args
                    }
                })
        return content.strip(), tool_calls_out

    def get_embedding(self, text: str, model: str) -> dict:
        clean_text = re.sub(r'[\x00-\x1F\x7F-\x9F]', '', text)
        clean_text = clean_text.replace('\n', ' ').replace('\r', '').strip()
        if not clean_text: clean_text = "none"
        try:
            response = self.client.embeddings.create(input=[clean_text], model=model)
            return {"dense": response.data[0].embedding, "sparse": {}}
        except Exception:
            try:
                fallback_text = clean_text[:50]
                response = self.client.embeddings.create(input=[fallback_text], model=model)
                return {"dense": response.data[0].embedding, "sparse": {}}
            except Exception:
                return {"dense": [], "sparse": {}}

class LlamaCppProvider(OpenAICompatibleProvider):
    """llama.cpp server 專用 Provider — response_format 自動降級。
    """

    def generate_chat(self, messages: list, model: str, temperature: float = 0.0, response_format: dict | None = None, tools: list | None = None, tool_choice: str | dict = "auto") -> tuple[str, list]:
        kwargs = {
            "model": model,
            "messages": self._normalize_messages(messages),
            "temperature": temperature
        }
        if tools:
            kwargs["tools"] = tools

        if response_format:
            # 先嘗試 json_schema（新版 llama.cpp 支援）
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "dynamic_schema",
                    "schema": response_format,
                    "strict": False
                }
            }
            try:
                response = self.client.chat.completions.create(**kwargs)
                msg = response.choices[0].message
            except Exception:
                # 降級為 json_object（舊版 llama.cpp）
                kwargs["response_format"] = {"type": "json_object"}
                response = self.client.chat.completions.create(**kwargs)
                msg = response.choices[0].message
        else:
            response = self.client.chat.completions.create(**kwargs)
            msg = response.choices[0].message

        content = msg.content or ''
        tool_calls_out = []
        if getattr(msg, 'tool_calls', None):
            import json
            for tc in msg.tool_calls:
                args = tc.function.arguments
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                tool_calls_out.append({
                    "id": getattr(tc, "id", f"call_{tc.function.name}"),
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": args
                    }
                })
        return content.strip(), tool_calls_out


class LLMRouter:
    def __init__(self):
        self.routes = {}

    def register_route(self, task_key: str, provider: ILLMProvider, model_name: str):
        self.routes[task_key] = {"provider": provider, "model": model_name}

    # 【核心修正】：Router 層級開放參數
    def generate(self, task_key: str, messages: list, temperature: float = 0.0, response_format: dict = None) -> str:
        route = self.routes.get(task_key)
        if not route:
            raise ValueError(f"[Router] 找不到任務 '{task_key}' 的路由設定。請確認已註冊。")

        SystemLogger.log_llm_prompt(task_key, route["model"], messages)
        response_text, _ = route["provider"].generate_chat(messages, route["model"], temperature, response_format)

        # 若有 response_format 但模型回傳無法解析的純文字，自動重試一次。
        # 雲端代理模型（如 deepseek-v3.1:671b-cloud）有時會忽略 format 參數直接回覆純文字。
        # 注意：合法回傳可能是 [] 或 {}，需用 json.loads 驗證而非單純檢查 { 是否存在。
        def _is_valid_json(text: str) -> bool:
            if not text:
                return False
            try:
                import json as _json
                _json.loads(text)
                return True
            except (ValueError, TypeError):
                return False

        if response_format and not _is_valid_json(response_text):
            SystemLogger.log_error(
                "LLMRouter",
                f"[{task_key}] 非 JSON 回應，自動重試。前100字: {response_text[:100]!r}"
            )
            retry_msgs = list(messages) + [
                {"role": "assistant", "content": response_text},
                {
                    "role": "user",
                    "content": (
                        "[系統警告] 你的上一則回覆格式錯誤，無法解析為合法 JSON。"
                        "請將上一則回覆的內容原封不動地重新包裝為合法 JSON 格式，"
                        "禁止修改內容、禁止重新生成新的回覆、禁止任何前導文字或 Markdown 格式。"
                    ),
                },
            ]
            response_text, _ = route["provider"].generate_chat(
                retry_msgs, route["model"], max(temperature * 0.5, 0.1), response_format
            )

        SystemLogger.log_llm_response(task_key, route["model"], response_text)
        return response_text

    def generate_with_tools(self, task_key: str, messages: list, tools: list | None = None, temperature: float = 0.0, tool_choice: str | dict = "auto", response_format: dict | None = None) -> tuple[str, list]:
        route = self.routes.get(task_key)
        if not route:
            raise ValueError(f"[Router] 找不到任務 '{task_key}' 的路由設定。請確認已註冊。")

        SystemLogger.log_llm_prompt(task_key, route["model"], messages, tools)
        response_text, tool_calls = route["provider"].generate_chat(messages, route["model"], temperature, response_format, tools, tool_choice)
        log_content = f"Content: {response_text}, Tools: {tool_calls}"
        SystemLogger.log_llm_response(task_key, route["model"], log_content)

        return response_text, tool_calls

    def generate_json(
        self,
        task_key: str,
        messages: list,
        schema: dict = None,
        temperature: float = 0.1,
    ) -> dict:
        """嘗試取得結構化 JSON 輸出，失敗時自動降級並嘗試提取。

        若有提供 schema，先以 response_format 呼叫；若 Provider 不支援則降級為純文字
        再以 JSONDecoder 提取第一個 JSON 物件。無論何種情況失敗皆回傳空 dict {}。

        Args:
            task_key: 路由任務鍵
            messages: 對話訊息列表 [{"role": ..., "content": ...}]
            schema: JSON Schema（可選）
            temperature: 溫度參數（預設 0.1）

        Returns:
            解析後的 dict，失敗時回傳 {}
        """
        try:
            raw = self.generate(task_key, messages, temperature=temperature, response_format=schema)
        except Exception:
            if schema is not None:
                # Provider 不支援 response_format，降級為純文字
                try:
                    raw = self.generate(task_key, messages, temperature=temperature)
                except Exception:
                    return {}
            else:
                return {}

        start = raw.find('{')
        if start == -1:
            start = raw.find('[')
        if start == -1:
            SystemLogger.log_error("generate_json", f"回應中找不到 JSON 物件，前100字: {raw[:100]!r}")
            return {}
        try:
            parsed, _ = json.JSONDecoder().raw_decode(raw, start)
            return parsed if isinstance(parsed, dict) else {}
        except Exception as e:
            SystemLogger.log_error("generate_json", f"JSON 解析失敗: {e}，前200字: {raw[:200]!r}")
            return {}
