# 【環境假設】：Python 3.12, Streamlit 1.30+。瘦客戶端進入點。
# 所有業務邏輯已遷移至 FastAPI 後端，此處僅負責 UI 渲染與 API 呼叫。
import streamlit as st
import requests
import warnings

from ui_settings import render_settings_page
from ui_chat import render_chat_page
from ui_db_manager import render_db_manager_page
from ui_log_viewer import render_log_viewer_page

warnings.filterwarnings("ignore", message="coroutine 'expire_cache' was never awaited")

st.set_page_config(page_title="具備情境記憶的 LLM", page_icon="🧠", layout="wide")

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
    st.error("⚠️ FastAPI 後端未啟動！請先執行: `uvicorn api.main:app --port 8000`")

# ==========================================
# 頁面路由分發 (View Controller)
# ==========================================
with st.sidebar:
    st.title("導覽列")
    current_page = st.radio("選擇功能區塊", ["💬 對話大廳", "🧠 記憶庫與資料庫管理", "⚙️ 系統與路由設定", "📋 Log 檢視器"])
    st.divider()

if current_page == "💬 對話大廳":
    render_chat_page(API_BASE, user_prefs)

elif current_page == "🧠 記憶庫與資料庫管理":
    render_db_manager_page(API_BASE, user_prefs)

elif current_page == "⚙️ 系統與路由設定":
    render_settings_page(API_BASE)

elif current_page == "📋 Log 檢視器":
    render_log_viewer_page(API_BASE)
