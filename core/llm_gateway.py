# 【環境假設】：Python 3.12, ollama, openai, numpy, onnxruntime, transformers 庫可用。
# 已在當前目錄透過 hf download 取得 BGE-M3 的 INT8 量化檔 (*.onnx)
import inspect
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
CHAT_JSON_MAX_TOKENS = 768

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
    def generate_chat(self, messages: list, model: str, temperature: float = 0.0, response_format: dict | None = None, tools: list | None = None, tool_choice: str | dict = "auto", max_tokens: int | None = None, logit_bias: dict | None = None) -> tuple[str, list]:
        raise NotImplementedError
        
    def get_embedding(self, text: str, model: str) -> dict:
        raise NotImplementedError

class OllamaProvider(ILLMProvider):
    def __init__(self, host: str = "http://localhost:11434"):
        self.host = (host or "http://localhost:11434").rstrip("/")
        self._client = ollama.Client(host=self.host)
        self._openai_client = OpenAI(api_key="ollama", base_url=self._openai_base_url(self.host))

    @staticmethod
    def _openai_base_url(host: str) -> str:
        root = (host or "http://localhost:11434").rstrip("/")
        if root.endswith("/api"):
            root = root[:-4]
        if root.endswith("/v1"):
            return root
        return f"{root}/v1"

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

    def _normalize_messages_openai(self, messages: list) -> list:
        """Ollama OpenAI-compatible 格式正規化。"""
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

    @staticmethod
    def _extract_openai_response(response) -> tuple[str, list]:
        choice = response.choices[0]
        msg = choice.message
        content = msg.content or ""
        tool_calls_out = []
        if getattr(msg, "tool_calls", None):
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
                        "arguments": args,
                    },
                })
        return content.strip(), tool_calls_out

    def _generate_chat_openai_compatible(
        self,
        messages: list,
        model: str,
        temperature: float = 0.0,
        response_format: dict | None = None,
        tools: list | None = None,
        tool_choice: str | dict = "auto",
        max_tokens: int | None = None,
        logit_bias: dict | None = None,
    ) -> tuple[str, list]:
        kwargs = {
            "model": model,
            "messages": self._normalize_messages_openai(messages),
            "temperature": temperature,
        }
        if max_tokens:
            kwargs["max_tokens"] = int(max_tokens)
        if response_format:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "dynamic_schema",
                    "schema": response_format,
                    "strict": False,
                },
            }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        if logit_bias:
            kwargs["logit_bias"] = logit_bias

        response = self._openai_client.chat.completions.create(**kwargs)
        return self._extract_openai_response(response)

    def _generate_chat_native(
        self,
        messages: list,
        model: str,
        temperature: float = 0.0,
        response_format: dict | None = None,
        tools: list | None = None,
        max_tokens: int | None = None,
    ) -> tuple[str, list]:
        options = {"temperature": temperature}
        if max_tokens:
            options["num_predict"] = int(max_tokens)
        kwargs = {
            "model": model,
            "messages": self._normalize_messages(messages),
            "options": options,
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

    def generate_chat(self, messages: list, model: str, temperature: float = 0.0, response_format: dict | None = None, tools: list | None = None, tool_choice: str | dict = "auto", max_tokens: int | None = None, logit_bias: dict | None = None) -> tuple[str, list]:
        if logit_bias:
            try:
                return self._generate_chat_openai_compatible(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    response_format=response_format,
                    tools=tools,
                    tool_choice=tool_choice,
                    max_tokens=max_tokens,
                    logit_bias=logit_bias,
                )
            except Exception as exc:
                SystemLogger.log_error(
                    "OllamaProvider",
                    f"OpenAI-compatible logit_bias 呼叫失敗，改用 native Ollama 降級: {type(exc).__name__}: {exc}",
                    details={"model": model, "base_url": self._openai_base_url(self.host)},
                )

        return self._generate_chat_native(
            messages=messages,
            model=model,
            temperature=temperature,
            response_format=response_format,
            tools=tools,
            max_tokens=max_tokens,
        )

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

    def generate_chat(self, messages: list, model: str, temperature: float = 0.0, response_format: dict | None = None, tools: list | None = None, tool_choice: str | dict = "auto", max_tokens: int | None = None, logit_bias: dict | None = None) -> tuple[str, list]:
        kwargs = {
            "model": model,
            "messages": self._normalize_messages(messages),
            "temperature": temperature,
        }
        if max_tokens:
            kwargs["max_tokens"] = int(max_tokens)
        if logit_bias:
            kwargs["logit_bias"] = logit_bias
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
                if "logit_bias" in err_str and any(token in err_str.lower() for token in ("unsupported", "unknown", "extra", "not supported")):
                    if attempt < max_retries and "logit_bias" in kwargs:
                        kwargs.pop("logit_bias", None)
                        SystemLogger.log_error(
                            "LLMRouter",
                            f"[{model}] provider 不支援 logit_bias，已移除後重試",
                        )
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

    def generate_chat(self, messages: list, model: str, temperature: float = 0.0, response_format: dict | None = None, tools: list | None = None, tool_choice: str | dict = "auto", max_tokens: int | None = None, logit_bias: dict | None = None) -> tuple[str, list]:
        kwargs = {
            "model": model,
            "messages": self._normalize_messages(messages),
            "temperature": temperature
        }
        if max_tokens:
            kwargs["max_tokens"] = int(max_tokens)
        if logit_bias:
            kwargs["logit_bias"] = logit_bias
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

    @staticmethod
    def _structured_chat_max_tokens(
        task_key: str,
        response_format: dict | None,
        log_context: dict | None,
    ) -> int | None:
        """最終角色 JSON 回覆很短，限制輸出可避免模型發散時長篇續寫。"""
        if task_key == "chat" and response_format and log_context:
            return CHAT_JSON_MAX_TOKENS
        return None

    @staticmethod
    def _provider_accepts_param(provider: ILLMProvider, param_name: str) -> bool:
        try:
            return param_name in inspect.signature(provider.generate_chat).parameters
        except (TypeError, ValueError):
            return False

    def _generate_chat_with_optional_limit(
        self,
        provider: ILLMProvider,
        messages: list,
        model: str,
        temperature: float,
        response_format: dict | None,
        tools: list | None = None,
        tool_choice: str | dict = "auto",
        max_tokens: int | None = None,
        logit_bias: dict | None = None,
    ) -> tuple[str, list]:
        optional_kwargs = {}
        if max_tokens and self._provider_accepts_param(provider, "max_tokens"):
            optional_kwargs["max_tokens"] = max_tokens
        if logit_bias and self._provider_accepts_param(provider, "logit_bias"):
            optional_kwargs["logit_bias"] = logit_bias
        elif logit_bias:
            SystemLogger.log_system_event(
                "LLMRouter",
                f"[{model}] provider 不支援 logit_bias，已忽略本次開場抑制 bias",
            )
        return provider.generate_chat(
            messages,
            model,
            temperature,
            response_format,
            tools,
            tool_choice,
            **optional_kwargs,
        )

    # 【核心修正】：Router 層級開放參數
    def generate(
        self,
        task_key: str,
        messages: list,
        temperature: float = 0.0,
        response_format: dict = None,
        log_context: dict | None = None,
        logit_bias: dict | None = None,
    ) -> str:
        route = self.routes.get(task_key)
        if not route:
            raise ValueError(f"[Router] 找不到任務 '{task_key}' 的路由設定。請確認已註冊。")

        SystemLogger.log_llm_prompt(task_key, route["model"], messages, log_context=log_context)
        max_tokens = self._structured_chat_max_tokens(task_key, response_format, log_context)
        response_text, _ = self._generate_chat_with_optional_limit(
            route["provider"],
            messages,
            route["model"],
            temperature,
            response_format,
            max_tokens=max_tokens,
            logit_bias=logit_bias,
        )

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

        def _looks_like_document_dump(text: str) -> bool:
            stripped = (text or "").strip()
            if not stripped:
                return False
            lines = stripped.splitlines()
            heading_count = sum(1 for line in lines if line.lstrip().startswith("#"))
            bullet_count = sum(1 for line in lines if line.lstrip().startswith(("- ", "* ")))
            has_code_fence = "```" in stripped
            has_shell_doc = any(token in stripped.lower() for token in (
                "docker-compose", "sudo pip", "pip install", "command] [args",
            ))
            long_markdown_doc = len(stripped) > 500 and (heading_count >= 2 or bullet_count >= 8)
            return has_shell_doc or has_code_fence or long_markdown_doc

        def _looks_like_group_speaker_leak(text: str, ctx: dict | None) -> bool:
            if not text or not ctx or ctx.get("session_mode") != "group":
                return False
            current_id = str(ctx.get("current_character_id") or "")
            participants = ctx.get("participants") or []
            for participant in participants:
                cid = str(participant.get("character_id") or "")
                name = str(participant.get("name") or "")
                if not cid or cid == current_id:
                    continue
                if f"|{cid}]:" in text or (name and f"[{name}|" in text):
                    return True
            return False

        if response_format and not _is_valid_json(response_text):
            original_prompt = "\n\n".join(
                f"[{msg.get('role', 'unknown')}]\n{msg.get('content', '')}"
                for msg in messages
            )
            retry_reason = ""
            if _looks_like_group_speaker_leak(response_text, log_context):
                retry_strategy = "regenerate"
                retry_reason = "group_speaker_leak"
            elif _looks_like_document_dump(response_text):
                retry_strategy = "regenerate"
                retry_reason = "document_dump"
            else:
                retry_strategy = "preserve_previous"
                retry_reason = "format_only"
            if retry_strategy == "preserve_previous":
                retry_warning = (
                    "<retry_instruction reason=\"invalid_json\" strategy=\"preserve_previous\">"
                    "你的上一則回覆格式錯誤，無法解析為合法 JSON。"
                    "上一則內容本身可用，請將上一則 assistant 回覆的內容原封不動地重新包裝為合法 JSON 格式，"
                    "禁止修改內容、禁止重新生成新的回覆、禁止任何前導文字或 Markdown 格式。"
                    "</retry_instruction>"
                )
            else:
                retry_warning = (
                    f"<retry_instruction reason=\"{retry_reason}\" strategy=\"regenerate\">"
                    "你的上一則回覆格式錯誤，且內容看起來混入無關文件或其他 AI 的發言。"
                    "請忽略上一則錯誤輸出，重新依照原始對話與系統指令回答。"
                    "你只能代表目前指定角色發言，禁止輸出其他 AI 的 speaker tag 或台詞。"
                    "輸出必須是合法 JSON，禁止任何前導文字、Markdown 或額外說明。"
                    "</retry_instruction>"
                )
            SystemLogger.log_error(
                f"LLMRouter/{task_key}",
                f"非 JSON 回應，自動重試。前100字: {response_text[:100]!r}",
                details={
                    "model": route["model"],
                    "temperature": temperature,
                    "retry_strategy": retry_strategy,
                    "retry_reason": retry_reason,
                    "max_tokens": max_tokens,
                    "response_preview": response_text[:1000],
                    "retry_warning": retry_warning,
                    "original_prompt": original_prompt,
                    "original_messages": messages,
                    "log_context": log_context or {},
                },
            )
            if retry_strategy == "preserve_previous":
                retry_msgs = list(messages) + [
                    {"role": "assistant", "content": response_text},
                    {"role": "user", "content": retry_warning},
                ]
            else:
                retry_msgs = list(messages)
                if retry_msgs and retry_msgs[-1].get("role") == "user":
                    retry_msgs[-1] = {
                        **retry_msgs[-1],
                        "content": retry_msgs[-1].get("content", "") + "\n\n" + retry_warning,
                    }
                else:
                    retry_msgs.append({"role": "user", "content": retry_warning})
            response_text, _ = self._generate_chat_with_optional_limit(
                route["provider"],
                retry_msgs,
                route["model"],
                max(temperature * 0.5, 0.1),
                response_format,
                max_tokens=max_tokens,
                logit_bias=logit_bias,
            )

        SystemLogger.log_llm_response(task_key, route["model"], response_text)
        return response_text

    def generate_with_tools(
        self,
        task_key: str,
        messages: list,
        tools: list | None = None,
        temperature: float = 0.0,
        tool_choice: str | dict = "auto",
        response_format: dict | None = None,
        log_context: dict | None = None,
        logit_bias: dict | None = None,
    ) -> tuple[str, list]:
        route = self.routes.get(task_key)
        if not route:
            raise ValueError(f"[Router] 找不到任務 '{task_key}' 的路由設定。請確認已註冊。")

        SystemLogger.log_llm_prompt(task_key, route["model"], messages, tools, log_context=log_context)
        response_text, tool_calls = self._generate_chat_with_optional_limit(
            route["provider"],
            messages,
            route["model"],
            temperature,
            response_format,
            tools,
            tool_choice,
            logit_bias=logit_bias,
        )
        log_content = f"Content: {response_text}, Tools: {tool_calls}"
        SystemLogger.log_llm_response(task_key, route["model"], log_content)

        return response_text, tool_calls

    def generate_json(
        self,
        task_key: str,
        messages: list,
        schema: dict = None,
        temperature: float = 0.1,
        log_context: dict | None = None,
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
            raw = self.generate(
                task_key, messages, temperature=temperature,
                response_format=schema, log_context=log_context,
            )
        except Exception:
            if schema is not None:
                # Provider 不支援 response_format，降級為純文字
                try:
                    raw = self.generate(task_key, messages, temperature=temperature, log_context=log_context)
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
