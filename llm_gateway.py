# 【環境假設】：Python 3.12, ollama, openai, numpy, onnxruntime, transformers 庫可用。
# 已在當前目錄透過 hf download 取得 BGE-M3 的 INT8 量化檔 (*.onnx)
import re
import ollama
from openai import OpenAI
import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer
import glob
from system_logger import SystemLogger

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
    # 【核心修正】：新增 response_format 參數
    def generate_chat(self, messages: list, model: str, temperature: float = 0.0, response_format: dict = None) -> str:
        raise NotImplementedError
        
    def get_embedding(self, text: str, model: str) -> dict:
        raise NotImplementedError

class OllamaProvider(ILLMProvider):
    def generate_chat(self, messages: list, model: str, temperature: float = 0.0, response_format: dict = None) -> str:
        kwargs = {
            "model": model,
            "messages": messages,
            "options": {"temperature": temperature}
        }
        # 【核心修正】：Ollama 原生支援直接將 Schema 字典傳入 format 參數
        if response_format:
            kwargs["format"] = response_format
            
        response = ollama.chat(**kwargs)
        return response['message']['content'].strip()

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
                w = float(weight)
                if token_str not in sparse_dict or w > sparse_dict[token_str]:
                    sparse_dict[token_str] = w
            return {"dense": dense_vec, "sparse": sparse_dict}
        else:
            try:
                response = ollama.embeddings(model=model, prompt=clean_text)
                return {"dense": response['embedding'], "sparse": {}}
            except Exception:
                try:
                    fallback_text = clean_text[:50]
                    response = ollama.embeddings(model=model, prompt=fallback_text)
                    return {"dense": response['embedding'], "sparse": {}}
                except Exception:
                    return {"dense": [], "sparse": {}}

class OpenAICompatibleProvider(ILLMProvider):
    def __init__(self, api_key: str, base_url: str = None):
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def generate_chat(self, messages: list, model: str, temperature: float = 0.0, response_format: dict = None) -> str:
        kwargs = {
            "model": model,
            "messages": messages,
            "temperature": temperature
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
            
        response = self.client.chat.completions.create(**kwargs)
        return response.choices[0].message.content.strip()

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
        response_text = route["provider"].generate_chat(messages, route["model"], temperature, response_format)
        SystemLogger.log_llm_response(task_key, route["model"], response_text)
        
        return response_text