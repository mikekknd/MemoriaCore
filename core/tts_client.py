"""Minimax TTS 客戶端 — core service layer。

功能：文字 → 音頻 bytes（不寫檔，由呼叫端決定如何處置）。
整合點：api/routers/chat_ws.py 在回覆完成後呼叫 synthesize()，
        將 bytes 以 base64 透過 WebSocket tts_audio 事件送回 client。

設定項目（user_prefs.json）：
    tts_enabled         : bool  — 全局開關（預設 false）
    minimax_api_key     : str   — Minimax API Key
    minimax_voice_id    : str   — 聲音 ID
    minimax_model       : str   — 模型（預設 speech-2.8-hd）
    minimax_speed       : float — 語速（預設 1.0）
    minimax_vol         : float — 音量（預設 1.0）
    minimax_pitch       : int   — 音調（預設 0）
"""
import asyncio
import json
import ssl
from typing import Optional

import websockets

from core.system_logger import SystemLogger


# ════════════════════════════════════════════════════════════
# SECTION: MinimaxTTSClient
# ════════════════════════════════════════════════════════════

class MinimaxTTSClient:
    """
    Minimax WebSocket TTS 客戶端。

    設計為 stateless service：同一實例可複用，每次 synthesize() 開新 WS 連線。
    """

    WS_URL = "wss://api.minimax.io/ws/v1/t2a_v2"
    DEFAULT_MODEL = "speech-2.8-hd"
    DEFAULT_VOICE = "moss_audio_7c2b39d9-1006-11f1-b9c4-4ea5324904c7"

    def __init__(
        self,
        api_key: str,
        voice_id: str = DEFAULT_VOICE,
        model: str = DEFAULT_MODEL,
        speed: float = 1.0,
        vol: float = 1.0,
        pitch: int = 0,
        sample_rate: int = 32000,
        bitrate: int = 128000,
        fmt: str = "mp3",
        recv_timeout: float = 30.0,
    ):
        self.api_key = api_key
        self.voice_id = voice_id
        self.model = model
        self.speed = speed
        self.vol = vol
        self.pitch = pitch
        self.sample_rate = sample_rate
        self.bitrate = bitrate
        self.fmt = fmt
        self.recv_timeout = recv_timeout
        self._ssl_ctx = ssl.create_default_context()

    # ════════════════════════════════════════════════════════
    # SECTION: 主要合成介面
    # ════════════════════════════════════════════════════════

    async def synthesize(self, text: str) -> Optional[bytes]:
        """
        將文字合成為音頻，回傳原始 bytes（mp3/其他格式）。

        Returns:
            bytes — 成功；None — 失敗（已 log）。
        """
        if not text or not self.api_key:
            return None

        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            async with websockets.connect(
                self.WS_URL, additional_headers=headers, ssl=self._ssl_ctx
            ) as ws:
                # 1. task_start
                await ws.send(json.dumps(self._build_start_params()))

                # 2. 等待 task_started
                raw = await asyncio.wait_for(ws.recv(), timeout=self.recv_timeout)
                start_data = _safe_json(raw)
                event = start_data.get("event", "")
                if event in ("error", "task_failed"):
                    SystemLogger.log_error("TTS", f"task_start 失敗: {start_data}")
                    return None
                if event not in ("task_start", "task_started", "connected_success"):
                    SystemLogger.log_error("TTS", f"意外的 start event: {start_data}")
                    return None

                # 3. 送文字 + 送 task_finish（通知 server 文字輸入完畢）
                await ws.send(json.dumps({"event": "task_continue", "text": text}))
                await ws.send(json.dumps({"event": "task_finish"}))

                # 4. 接收音頻 chunks
                chunks: list[bytes] = []
                while True:
                    raw = await asyncio.wait_for(ws.recv(), timeout=self.recv_timeout)
                    data = _safe_json(raw)
                    ev = data.get("event", "")

                    if ev == "task_finish":
                        break
                    elif ev == "task_continued":
                        hex_chunk = data.get("data", {}).get("audio", "")
                        if hex_chunk:
                            chunks.append(bytes.fromhex(hex_chunk))
                    elif ev in ("task_failed", "error"):
                        SystemLogger.log_error("TTS", f"{ev}: {data}")
                        return None

        except asyncio.TimeoutError:
            SystemLogger.log_error("TTS", "接收音頻 timeout")
            return None
        except websockets.exceptions.ConnectionClosedOK:
            pass  # 有時 server 會在 task_finish 後直接關閉連線，正常
        except Exception as e:
            SystemLogger.log_error("TTS", f"{type(e).__name__}: {e}")
            return None

        if not chunks:
            SystemLogger.log_error("TTS", "未收到任何音頻資料")
            return None

        return b"".join(chunks)

    # ════════════════════════════════════════════════════════
    # SECTION: 工廠方法 — 從 user_prefs 建立實例
    # ════════════════════════════════════════════════════════

    @classmethod
    def from_prefs(cls, prefs: dict) -> Optional["MinimaxTTSClient"]:
        """
        從 user_prefs dict 建立 TTS client。
        若 tts_enabled=False 或 minimax_api_key 為空，回傳 None。
        """
        if not prefs.get("tts_enabled", False):
            return None
        api_key = prefs.get("minimax_api_key", "").strip()
        if not api_key:
            return None
        return cls(
            api_key=api_key,
            voice_id=prefs.get("minimax_voice_id", cls.DEFAULT_VOICE),
            model=prefs.get("minimax_model", cls.DEFAULT_MODEL),
            speed=float(prefs.get("minimax_speed", 1.0)),
            vol=float(prefs.get("minimax_vol", 1.0)),
            pitch=int(prefs.get("minimax_pitch", 0)),
        )

    # ════════════════════════════════════════════════════════
    # SECTION: 私有工具
    # ════════════════════════════════════════════════════════

    def _build_start_params(self) -> dict:
        return {
            "event": "task_start",
            "model": self.model,
            "voice_setting": {
                "voice_id": self.voice_id,
                "speed": self.speed,
                "vol": self.vol,
                "pitch": self.pitch,
                "text_normalization": False,
            },
            "audio_setting": {
                "sample_rate": self.sample_rate,
                "bitrate": self.bitrate,
                "format": self.fmt,
                "channel": 1,
            },
            "stream": True,
        }


# ════════════════════════════════════════════════════════════
# SECTION: 工具函式
# ════════════════════════════════════════════════════════════

def _safe_json(raw) -> dict:
    """安全解析 JSON，非字串或解析失敗時回傳空 dict。"""
    if not isinstance(raw, (str, bytes)):
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}
