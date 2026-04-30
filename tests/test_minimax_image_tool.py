import base64
import json
import shutil
from pathlib import Path

import tools.minimax_image as minimax_image


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_generate_image_writes_file_and_returns_markdown(monkeypatch):
    image_bytes = b"fake-jpeg-bytes"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    test_root = Path("generated_images_test")
    if test_root.exists():
        shutil.rmtree(test_root)

    def fake_post(url, headers, json, timeout):
        assert url == "https://api.minimax.io/v1/image_generation"
        assert headers["Authorization"] == "Bearer test-key"
        assert json["model"] == "image-01"
        assert json["prompt"] == "一座未來城市"
        assert json["aspect_ratio"] == "16:9"
        assert json["response_format"] == "base64"
        return _FakeResponse({"data": {"image_base64": [encoded]}})

    monkeypatch.setattr(minimax_image, "_GENERATED_ROOT", test_root)
    monkeypatch.setattr(minimax_image, "_get_minimax_key", lambda: "test-key")
    monkeypatch.setattr(minimax_image.requests, "post", fake_post)

    raw = minimax_image.generate_image(
        "一座未來城市",
        "16:9",
        runtime_context={"user_id": "42", "session_id": "session-1"},
    )
    result = json.loads(raw)
    image = result["generated_images"][0]

    assert image["url"].startswith("/api/v1/chat/generated-images/session-1/")
    assert image["markdown"] == f"![generated image]({image['url']})"

    image_id = image["url"].rsplit("/", 1)[-1].removesuffix(".jpeg")
    saved = minimax_image.generated_image_path("42", "session-1", image_id)
    assert saved.read_bytes() == image_bytes
    shutil.rmtree(test_root)


def test_generate_self_portrait_injects_visual_prompt(monkeypatch):
    image_bytes = b"fake-jpeg-bytes"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    captured = {}
    test_root = Path("generated_images_test")
    if test_root.exists():
        shutil.rmtree(test_root)

    def fake_post(url, headers, json, timeout):
        captured["prompt"] = json["prompt"]
        return _FakeResponse({"data": {"image_base64": [encoded]}})

    monkeypatch.setattr(minimax_image, "_GENERATED_ROOT", test_root)
    monkeypatch.setattr(minimax_image, "_get_minimax_key", lambda: "test-key")
    monkeypatch.setattr(minimax_image.requests, "post", fake_post)

    raw = minimax_image.generate_self_portrait(
        "半身自畫像，微笑",
        "1:1",
        runtime_context={
            "user_id": "42",
            "session_id": "session-1",
            "visual_prompt": "九尾狐娘，白髮，金色眼睛，九條狐尾，和風服飾，anime portrait",
        },
    )
    result = json.loads(raw)

    assert "九尾狐娘" in captured["prompt"]
    assert "半身自畫像，微笑" in captured["prompt"]
    assert "not a generic AI, robot" in captured["prompt"]
    assert result["generated_images"][0]["prompt"] == "半身自畫像，微笑"
    assert result["generated_images"][0]["final_prompt"] == captured["prompt"]
    shutil.rmtree(test_root)


def test_generate_image_does_not_inject_visual_prompt(monkeypatch):
    image_bytes = b"fake-jpeg-bytes"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    captured = {}
    test_root = Path("generated_images_test")
    if test_root.exists():
        shutil.rmtree(test_root)

    def fake_post(url, headers, json, timeout):
        captured["prompt"] = json["prompt"]
        return _FakeResponse({"data": {"image_base64": [encoded]}})

    monkeypatch.setattr(minimax_image, "_GENERATED_ROOT", test_root)
    monkeypatch.setattr(minimax_image, "_get_minimax_key", lambda: "test-key")
    monkeypatch.setattr(minimax_image.requests, "post", fake_post)

    minimax_image.generate_image(
        "一台紅色跑車",
        "1:1",
        runtime_context={
            "user_id": "42",
            "session_id": "session-1",
            "visual_prompt": "九尾狐娘",
        },
    )

    assert captured["prompt"] == "一台紅色跑車"
    shutil.rmtree(test_root)


def test_generate_image_without_key_returns_error(monkeypatch):
    monkeypatch.setattr(minimax_image, "_get_minimax_key", lambda: "")

    raw = minimax_image.generate_image("一張貓咪照片")

    result = json.loads(raw)
    assert "MiniMax API Key" in result["error"]


def test_append_generated_images_adds_missing_markdown():
    tool_results = [
        {
            "tool_name": "generate_image",
            "result": json.dumps(
                {
                    "generated_images": [
                        {
                            "url": "/api/v1/chat/generated-images/s/abc.jpeg",
                            "markdown": "![generated image](/api/v1/chat/generated-images/s/abc.jpeg)",
                        }
                    ]
                }
            ),
        }
    ]

    reply = minimax_image.append_generated_images("已經為你生成圖片。", tool_results)

    assert "已經為你生成圖片。" in reply
    assert "![generated image](/api/v1/chat/generated-images/s/abc.jpeg)" in reply


def test_strip_generated_images_removes_known_markdown():
    tool_results = [
        {
            "tool_name": "generate_image",
            "result": json.dumps(
                {
                    "generated_images": [
                        {
                            "url": "/api/v1/chat/generated-images/s/abc.jpeg",
                            "markdown": "![generated image](/api/v1/chat/generated-images/s/abc.jpeg)",
                        }
                    ]
                }
            ),
        }
    ]

    reply = minimax_image.strip_generated_images(
        "沿用剛剛那張圖。\n\n![generated image](/api/v1/chat/generated-images/s/abc.jpeg)",
        tool_results,
    )

    assert reply == "沿用剛剛那張圖。"


def test_strip_generated_images_removes_same_url_with_different_alt_text():
    tool_results = [
        {
            "tool_name": "generate_image",
            "result": json.dumps(
                {
                    "generated_images": [
                        {"url": "/api/v1/chat/generated-images/s/abc.jpeg"}
                    ]
                }
            ),
        }
    ]

    reply = minimax_image.strip_generated_images(
        "我只評論前面那張。\n\n![same image](/api/v1/chat/generated-images/s/abc.jpeg)",
        tool_results,
    )

    assert reply == "我只評論前面那張。"
