"""建立 YouTubeBridge 使用的 YouTube OAuth refresh token。"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import secrets
import sys
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen
import webbrowser


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OAUTH_DIR = BRIDGE_ROOT.parent / "runtime" / "YouTubeBridge" / "oauth"
DEFAULT_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
DEFAULT_TOKEN_URI = "https://oauth2.googleapis.com/token"
YOUTUBE_READONLY_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _client_secret_section(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("installed", "web"):
        section = payload.get(key)
        if isinstance(section, dict):
            return section
    return payload


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def load_client_secret(oauth_dir: Path) -> dict[str, str]:
    path = oauth_dir / "client_secret.json"
    section = _client_secret_section(_read_json(path))
    client_id = _first_text(section.get("client_id"))
    client_secret = _first_text(section.get("client_secret"))
    if not client_id or not client_secret:
        raise RuntimeError(f"{path} 缺少 client_id 或 client_secret")
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "auth_uri": _first_text(section.get("auth_uri"), DEFAULT_AUTH_URI),
        "token_uri": _first_text(section.get("token_uri"), DEFAULT_TOKEN_URI),
    }


def build_authorization_url(*, client_secret: dict[str, str], redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_secret["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": YOUTUBE_READONLY_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return f"{client_secret['auth_uri']}?{urlencode(params)}"


def post_token_form(url: str, data: dict[str, str], timeout: float) -> dict[str, Any]:
    body = urlencode(data).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"OAuth token exchange HTTP {exc.code}: {detail}") from exc
    try:
        result = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("OAuth token exchange 回傳不是 JSON") from exc
    if not isinstance(result, dict):
        raise RuntimeError("OAuth token exchange 回傳格式不正確")
    return result


def exchange_code_and_write_token(
    *,
    oauth_dir: Path,
    code: str,
    redirect_uri: str,
    fallback_channel_id: str,
    post_token: Callable[[str, dict[str, str], float], dict[str, Any]] = post_token_form,
) -> Path:
    oauth_dir.mkdir(parents=True, exist_ok=True)
    client_secret = load_client_secret(oauth_dir)
    token_path = oauth_dir / "token.json"
    token_response = post_token(
        client_secret["token_uri"],
        {
            "code": code,
            "client_id": client_secret["client_id"],
            "client_secret": client_secret["client_secret"],
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        30.0,
    )
    existing = _read_json(token_path)
    refresh_token = _first_text(token_response.get("refresh_token"), existing.get("refresh_token"))
    if not refresh_token:
        raise RuntimeError(
            "Google 沒有回傳 refresh_token；請確認使用 prompt=consent / access_type=offline，"
            "或先到 Google 帳號安全性撤銷既有授權後重試。"
        )
    channel_id = _first_text(fallback_channel_id, existing.get("fallback_channel_id"))
    payload = {
        "refresh_token": refresh_token,
        "fallback_channel_id": channel_id,
        "scope": _first_text(token_response.get("scope"), existing.get("scope"), YOUTUBE_READONLY_SCOPE),
        "token_uri": client_secret["token_uri"],
    }
    token_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        token_path.chmod(0o600)
    except OSError:
        pass
    return token_path


def run_local_oauth_flow(*, oauth_dir: Path, fallback_channel_id: str, open_browser: bool, timeout_seconds: int) -> Path:
    client_secret = load_client_secret(oauth_dir)
    state = secrets.token_urlsafe(24)
    result: dict[str, str] = {}

    class OAuthCallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if query.get("state", [""])[0] != state:
                self.send_response(400)
                self.end_headers()
                self.wfile.write("OAuth state mismatch.".encode("utf-8"))
                return
            if query.get("error"):
                result["error"] = query.get("error", [""])[0]
            else:
                result["code"] = query.get("code", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                "<html><body><h1>YouTubeBridge OAuth 已收到授權結果</h1>"
                "<p>可以關閉這個分頁，回到 Codex。</p></body></html>".encode("utf-8")
            )

    with HTTPServer(("127.0.0.1", 0), OAuthCallbackHandler) as server:
        port = int(server.server_address[1])
        redirect_uri = f"http://localhost:{port}/"
        auth_url = build_authorization_url(client_secret=client_secret, redirect_uri=redirect_uri, state=state)
        print("請在瀏覽器完成 YouTube OAuth 授權。")
        print(auth_url)
        if open_browser:
            webbrowser.open(auth_url)
        server.timeout = timeout_seconds
        server.handle_request()

    if result.get("error"):
        raise RuntimeError(f"OAuth 授權失敗：{result['error']}")
    code = result.get("code", "").strip()
    if not code:
        raise RuntimeError("等待 OAuth callback 逾時，沒有收到 authorization code")
    return exchange_code_and_write_token(
        oauth_dir=oauth_dir,
        code=code,
        redirect_uri=redirect_uri,
        fallback_channel_id=fallback_channel_id,
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="建立 YouTubeBridge OAuth token.json")
    parser.add_argument("--oauth-dir", type=Path, default=DEFAULT_OAUTH_DIR)
    parser.add_argument("--fallback-channel-id", default="")
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--no-open-browser", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        token_path = run_local_oauth_flow(
            oauth_dir=args.oauth_dir,
            fallback_channel_id=args.fallback_channel_id,
            open_browser=not args.no_open_browser,
            timeout_seconds=max(30, int(args.timeout_seconds or 300)),
        )
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    print(f"[OK] 已寫入 {token_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
