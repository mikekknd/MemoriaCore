import sys
from pathlib import Path


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from tts_gpt_sovits import GptSoVitsTTSProvider


class FakeResponse:
    def __init__(self, status_code=200, content=b"audio-bytes", text=""):
        self.status_code = status_code
        self.content = content
        self.text = text


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, url, json, timeout):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        return self.response


def test_gpt_sovits_provider_posts_required_payload():
    fake_session = FakeSession(FakeResponse(content=b"wav-bytes"))
    provider = GptSoVitsTTSProvider(
        base_url="http://127.0.0.1:9880",
        session=fake_session,
    )

    result = provider.synthesize(
        "你好。",
        {
            "ref_audio_path": "voice.wav",
            "prompt_text": "參考文字。",
            "text_lang": "zh",
            "prompt_lang": "zh",
            "speed_factor": 1.2,
            "media_type": "wav",
        },
    )

    assert result.ok is True
    assert result.audio_bytes == b"wav-bytes"
    assert result.audio_format == "wav"
    call = fake_session.calls[0]
    assert call["url"] == "http://127.0.0.1:9880/tts"
    assert call["json"]["text"] == "你好。"
    assert call["json"]["text_lang"] == "zh"
    assert call["json"]["ref_audio_path"] == "voice.wav"
    assert call["json"]["prompt_lang"] == "zh"
    assert call["json"]["prompt_text"] == "參考文字。"
    assert call["json"]["media_type"] == "wav"
    assert call["json"]["streaming_mode"] is False
    assert call["json"]["speed_factor"] == 1.2


def test_gpt_sovits_provider_returns_failed_result_on_http_error():
    fake_session = FakeSession(FakeResponse(status_code=400, content=b"", text="bad request"))
    provider = GptSoVitsTTSProvider(session=fake_session)

    result = provider.synthesize(
        "你好。",
        {
            "ref_audio_path": "voice.wav",
            "text_lang": "zh",
            "prompt_lang": "zh",
        },
    )

    assert result.ok is False
    assert result.audio_bytes == b""
    assert "HTTP 400" in result.error
