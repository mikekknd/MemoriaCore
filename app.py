# 【環境假設】：Python 3.12, Streamlit 1.30+。瘦客戶端進入點。
# 所有業務邏輯已遷移至 FastAPI 後端，此處僅負責 UI 渲染與 API 呼叫。
import streamlit as st
import requests
import warnings

from ui import api_client
from ui.db_manager import render_db_manager_page
from ui.settings import render_settings_page
from ui.routing import render_routing_page
from ui.log_viewer import render_log_viewer_page
from ui.character import render_character_page
from ui.prompts import render_prompts_page
from ui.bots import render_bots_page
from core.i18n import DEFAULT_LOCALE, normalize_locale, t

warnings.filterwarnings("ignore", message="coroutine 'expire_cache' was never awaited")

st.set_page_config(page_title="MemoriaCore", page_icon="🧠", layout="wide")

# ==========================================
# API 基礎位址設定
# ==========================================
API_BASE = "http://localhost:8088/api/v1"


def _current_locale(user_prefs: dict | None = None) -> str:
    try:
        return normalize_locale((user_prefs or {}).get("ui_locale"))
    except ValueError:
        return DEFAULT_LOCALE


@st.cache_data(ttl=30, show_spinner=False)
def _load_public_ui_locale(api_base: str) -> str:
    """登入前只能讀公開 locale 端點，避免 Streamlit login 固定 fallback 繁中。"""
    try:
        response = requests.get(f"{api_base}/system/ui-locale", timeout=3)
        if response.ok:
            return normalize_locale(response.json().get("ui_locale"))
    except Exception:
        pass
    return DEFAULT_LOCALE


def _streamlit_login_locale() -> str:
    try:
        override = st.session_state.get("login_ui_locale")
        if override:
            return normalize_locale(override)
    except ValueError:
        st.session_state.pop("login_ui_locale", None)
    return _load_public_ui_locale(API_BASE)


def _ensure_streamlit_api_state() -> None:
    if "api_session" not in st.session_state:
        st.session_state.api_session = requests.Session()
    st.session_state.setdefault("api_csrf_token", "")
    st.session_state.setdefault("api_user", None)


def _logout_streamlit() -> None:
    try:
        api_client.post(f"{API_BASE}/auth/logout", timeout=5)
    except Exception:
        pass
    st.session_state.api_session = requests.Session()
    st.session_state.api_csrf_token = ""
    st.session_state.api_user = None
    _load_config.clear()
    _load_public_ui_locale.clear()


def _activate_streamlit_admin_session(payload: dict) -> bool:
    user = payload.get("user") or {}
    if user.get("role") != "admin":
        st.error("Streamlit 是管理後台，僅 admin 可使用。一般使用者請使用 /static/app.html。")
        st.session_state.api_session = requests.Session()
        return False

    st.session_state.api_csrf_token = payload.get("csrf_token", "")
    st.session_state.api_user = user
    _load_config.clear()
    return True


def _render_login() -> None:
    locale = _streamlit_login_locale()
    locale_options = ["zh-TW", "en-US"]
    selected_locale = st.selectbox(
        t("streamlit.login.language", locale),
        options=locale_options,
        index=locale_options.index(locale) if locale in locale_options else 0,
        format_func=lambda loc: t(f"profile.ui_locale.{loc}", locale),
        key="login_ui_locale_selector",
    )
    if selected_locale != locale:
        st.session_state.login_ui_locale = selected_locale
        st.rerun()

    st.title(t("streamlit.login.title", locale))
    st.caption(t("streamlit.login.caption", locale))
    register_url = API_BASE.replace("/api/v1", "/static/register.html")
    st.info(t("streamlit.login.first_admin_hint", locale, url=register_url))
    with st.form("streamlit_login"):
        username = st.text_input(t("auth.username", locale))
        password = st.text_input(t("auth.password", locale), type="password")
        submitted = st.form_submit_button(t("auth.login", locale))

    bypass_clicked = st.button(t("streamlit.login.admin_bypass", locale), use_container_width=True)
    if bypass_clicked:
        try:
            response = st.session_state.api_session.post(
                f"{API_BASE}/auth/bypass",
                timeout=8,
            )
        except requests.ConnectionError:
            st.error(t("streamlit.error.backend_down", locale))
            return
        except Exception as exc:
            st.error(t("streamlit.login.bypass_failed", locale, message=exc))
            return

        if not response.ok:
            try:
                detail = response.json().get("detail") or response.json().get("error", {}).get("message")
            except Exception:
                detail = response.text
            st.error(t("streamlit.login.bypass_failed", locale, message=detail or t("streamlit.login.bypass_disabled", locale)))
            return

        if _activate_streamlit_admin_session(response.json()):
            st.rerun()
        return

    if not submitted:
        return

    try:
        response = st.session_state.api_session.post(
            f"{API_BASE}/auth/login",
            json={"username": username, "password": password},
            timeout=8,
        )
    except requests.ConnectionError:
        st.error(t("streamlit.error.backend_down", locale))
        return
    except Exception as exc:
        st.error(t("auth.login_failed", locale) + f"：{exc}")
        return

    if not response.ok:
        try:
            detail = response.json().get("detail") or response.json().get("error", {}).get("message")
        except Exception:
            detail = response.text
        st.error(t("auth.login_failed", locale) + f"：{detail or response.status_code}")
        return

    if _activate_streamlit_admin_session(response.json()):
        st.rerun()


def _require_admin_login() -> bool:
    _ensure_streamlit_api_state()
    user = st.session_state.get("api_user")
    if not user:
        _render_login()
        return False

    try:
        response = api_client.get(f"{API_BASE}/auth/me", timeout=5)
    except requests.ConnectionError:
        st.error("⚠️ FastAPI 後端未啟動！請先執行: `uvicorn api.main:app --port 8088`")
        return False

    if response.status_code == 401:
        st.session_state.api_user = None
        st.session_state.api_csrf_token = ""
        _load_config.clear()
        _render_login()
        return False
    if not response.ok:
        st.error(f"無法驗證登入狀態：HTTP {response.status_code}")
        return False

    payload = response.json()
    if payload.get("role") != "admin":
        st.error("Streamlit 是管理後台，僅 admin 可使用。")
        return False
    st.session_state.api_user = payload
    if payload.get("csrf_token"):
        st.session_state.api_csrf_token = payload["csrf_token"]
    return True


@st.cache_data(ttl=30, show_spinner=False)
def _load_config(api_base: str, auth_cache_key: str) -> dict:
    """快取系統設定 30 秒，避免每次 Streamlit rerun 都重複呼叫 API。"""
    resp = api_client.get(f"{api_base}/system/config", timeout=3)
    if resp.ok:
        return resp.json()
    return {}


if not _require_admin_login():
    st.stop()

# 嘗試從 API 獲取設定（快取版）
try:
    user_prefs = _load_config(API_BASE, st.session_state.get("api_csrf_token", ""))
    if not user_prefs:
        st.warning("無法連線到 FastAPI 後端，部分功能可能無法使用。")
except requests.ConnectionError:
    user_prefs = {}
    st.error("⚠️ FastAPI 後端未啟動！請先執行: `uvicorn api.main:app --port 8088`")

# ==========================================
# 頁面路由分發 (View Controller)
# ==========================================
locale = _current_locale(user_prefs)
PAGES = [
    ("memory", t("streamlit.nav.memory", locale)),
    ("character", t("streamlit.nav.character", locale)),
    ("bots", t("streamlit.nav.bots", locale)),
    ("settings", t("streamlit.nav.settings", locale)),
    ("routing", t("streamlit.nav.routing", locale)),
    ("prompts", t("streamlit.nav.prompts", locale)),
    ("logs", t("streamlit.nav.logs", locale)),
]

with st.sidebar:
    st.title("MemoriaCore")
    user = st.session_state.get("api_user") or {}
    st.caption(f"{user.get('username', '')} ({user.get('role', 'admin')})")
    if st.button(t("dashboard.action.logout", locale)):
        _logout_streamlit()
        st.rerun()
    st.divider()
    current_page = st.radio(
        t("streamlit.nav.label", locale),
        [page_id for page_id, _ in PAGES],
        format_func=lambda page_id: next(label for pid, label in PAGES if pid == page_id),
        label_visibility="collapsed",
    )
    st.divider()

if current_page == "memory":
    render_db_manager_page(API_BASE, user_prefs)

elif current_page == "character":
    render_character_page(API_BASE, user_prefs)

elif current_page == "bots":
    render_bots_page(API_BASE, user_prefs)

elif current_page == "settings":
    render_settings_page(API_BASE, user_prefs)

elif current_page == "routing":
    render_routing_page(API_BASE, user_prefs)

elif current_page == "prompts":
    render_prompts_page(API_BASE, user_prefs)

elif current_page == "logs":
    render_log_viewer_page(API_BASE, user_prefs)
