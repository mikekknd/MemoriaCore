import importlib.util
import json
import sys
from pathlib import Path


BRIDGE_ROOT = Path(__file__).resolve().parents[1]
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))


def _load_helper_module():
    path = BRIDGE_ROOT / "scripts" / "create_youtube_oauth_token.py"
    spec = importlib.util.spec_from_file_location("create_youtube_oauth_token", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_exchange_code_writes_refresh_token_and_fallback_channel_id(tmp_path):
    module = _load_helper_module()
    oauth_dir = tmp_path / "oauth"
    oauth_dir.mkdir()
    (oauth_dir / "client_secret.json").write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                    "auth_uri": "https://accounts.google.com/o/oauth2/v2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost"],
                }
            }
        ),
        encoding="utf-8",
    )

    requests = []

    def fake_post(url, data, timeout):
        requests.append((url, data, timeout))
        return {
            "refresh_token": "refresh-token",
            "access_token": "access-token",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/youtube.readonly",
        }

    token_path = module.exchange_code_and_write_token(
        oauth_dir=oauth_dir,
        code="auth-code",
        redirect_uri="http://127.0.0.1:12345/oauth2callback",
        fallback_channel_id="UCRmntUEIcM3sy7N3pvio5Dg",
        post_token=fake_post,
    )

    assert token_path == oauth_dir / "token.json"
    assert requests == [
        (
            "https://oauth2.googleapis.com/token",
            {
                "code": "auth-code",
                "client_id": "client-id",
                "client_secret": "client-secret",
                "redirect_uri": "http://127.0.0.1:12345/oauth2callback",
                "grant_type": "authorization_code",
            },
            30.0,
        )
    ]
    saved = json.loads(token_path.read_text(encoding="utf-8"))
    assert saved["refresh_token"] == "refresh-token"
    assert saved["fallback_channel_id"] == "UCRmntUEIcM3sy7N3pvio5Dg"
    assert saved["scope"] == "https://www.googleapis.com/auth/youtube.readonly"
