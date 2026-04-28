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

warnings.filterwarnings("ignore", message="coroutine 'expire_cache' was never awaited")

st.set_page_config(page_title="MemoriaCore", page_icon="🧠", layout="wide")

# ==========================================
# API 基礎位址設定
# ==========================================
API_BASE = "http://localhost:8088/api/v1"


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


def _render_login() -> None:
    st.title("MemoriaCore 管理後台")
    st.caption("Streamlit 管理後台需要另外登入；瀏覽器在 /static/login.html 的 HttpOnly Cookie 不會傳給 Streamlit 的 Python requests。")
    register_url = API_BASE.replace("/api/v1", "/static/register.html")
    st.info(f"如果這是第一次使用、還沒有 admin 帳號，請先到 {register_url} 建立第一個帳號。第一個帳號會自動成為 admin。")
    with st.form("streamlit_login"):
        username = st.text_input("帳號")
        password = st.text_input("密碼", type="password")
        submitted = st.form_submit_button("登入")

    if not submitted:
        return

    try:
        response = st.session_state.api_session.post(
            f"{API_BASE}/auth/login",
            json={"username": username, "password": password},
            timeout=8,
        )
    except requests.ConnectionError:
        st.error("FastAPI 後端未啟動，請先啟動 8088。")
        return
    except Exception as exc:
        st.error(f"登入失敗：{exc}")
        return

    if not response.ok:
        try:
            detail = response.json().get("detail") or response.json().get("error", {}).get("message")
        except Exception:
            detail = response.text
        st.error(f"登入失敗：{detail or response.status_code}")
        return

    payload = response.json()
    user = payload.get("user") or {}
    if user.get("role") != "admin":
        st.error("Streamlit 是管理後台，僅 admin 可使用。一般使用者請使用 /static/app.html。")
        st.session_state.api_session = requests.Session()
        return

    st.session_state.api_csrf_token = payload.get("csrf_token", "")
    st.session_state.api_user = user
    _load_config.clear()
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
PAGES = [
    "🧠 記憶庫管理",
    "🎭 角色設定",
    "🤖 Bot 管理",
    "⚙️ 系統設定",
    "🔀 路由映射",
    "📝 Prompt 管理",
    "📋 Log 檢視器",
]

with st.sidebar:
    st.title("MemoriaCore")
    user = st.session_state.get("api_user") or {}
    st.caption(f"{user.get('username', '')} ({user.get('role', 'admin')})")
    if st.button("登出"):
        _logout_streamlit()
        st.rerun()
    st.divider()
    current_page = st.radio("導覽", PAGES, label_visibility="collapsed")
    st.divider()

if current_page == "🧠 記憶庫管理":
    render_db_manager_page(API_BASE, user_prefs)

elif current_page == "🎭 角色設定":
    render_character_page(API_BASE, user_prefs)

elif current_page == "🤖 Bot 管理":
    render_bots_page(API_BASE, user_prefs)

elif current_page == "⚙️ 系統設定":
    render_settings_page(API_BASE, user_prefs)

elif current_page == "🔀 路由映射":
    render_routing_page(API_BASE, user_prefs)

elif current_page == "📝 Prompt 管理":
    render_prompts_page(API_BASE, user_prefs)

elif current_page == "📋 Log 檢視器":
    render_log_viewer_page(API_BASE)
