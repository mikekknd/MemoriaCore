# 【環境假設】：Python 3.12, Streamlit 1.30+。瘦客戶端進入點。
# 所有業務邏輯已遷移至 FastAPI 後端，此處僅負責 UI 渲染與 API 呼叫。
import streamlit as st
import requests
import warnings

from ui.db_manager import render_db_manager_page
from ui.settings import render_settings_page
from ui.routing import render_routing_page
from ui.log_viewer import render_log_viewer_page
from ui.character import render_character_page
from ui.prompts import render_prompts_page

warnings.filterwarnings("ignore", message="coroutine 'expire_cache' was never awaited")

st.set_page_config(page_title="MemoriaCore", page_icon="🧠", layout="wide")

# ==========================================
# API 基礎位址設定
# ==========================================
API_BASE = "http://localhost:8088/api/v1"


@st.cache_data(ttl=30, show_spinner=False)
def _load_config(api_base: str) -> dict:
    """快取系統設定 30 秒，避免每次 Streamlit rerun 都重複呼叫 API。"""
    resp = requests.get(f"{api_base}/system/config", timeout=3)
    if resp.ok:
        return resp.json()
    return {}


# 嘗試從 API 獲取設定（快取版）
try:
    user_prefs = _load_config(API_BASE)
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
    "⚙️ 系統設定",
    "🔀 路由映射",
    "📝 Prompt 管理",
    "📋 Log 檢視器",
]

with st.sidebar:
    st.title("MemoriaCore")
    current_page = st.radio("導覽", PAGES, label_visibility="collapsed")
    st.divider()

if current_page == "🧠 記憶庫管理":
    render_db_manager_page(API_BASE, user_prefs)

elif current_page == "🎭 角色設定":
    render_character_page(API_BASE, user_prefs)

elif current_page == "⚙️ 系統設定":
    render_settings_page(API_BASE, user_prefs)

elif current_page == "🔀 路由映射":
    render_routing_page(API_BASE, user_prefs)

elif current_page == "📝 Prompt 管理":
    render_prompts_page(API_BASE, user_prefs)

elif current_page == "📋 Log 檢視器":
    render_log_viewer_page(API_BASE)
