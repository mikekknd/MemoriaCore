# 【環境假設】：Python 3.12, uvicorn, PyInstaller 打包環境
import signal
import sys
import os
import warnings
import multiprocessing

# huggingface_hub 舊版內部呼叫 resume_download 的 FutureWarning，來自第三方套件無法修改
warnings.filterwarnings("ignore", message=".*resume_download.*", category=FutureWarning)

# import 期間暫時忽略 SIGINT，防止 Windows console 把 Ctrl+C 傳播給剛啟動的子進程
_original_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
import uvicorn
signal.signal(signal.SIGINT, _original_sigint)  # 還原，讓 uvicorn 自行管理

# ── PyInstaller 路徑修正 ──────────────────────────────────
# 打包後 __file__ 指向 _internal/ 內部，相對路徑（user_prefs.json、
# memory_db_*.db、system_prompt.txt）會找不到正確位置。
# 強制將 CWD 切換到 exe 所在目錄，確保所有資料檔案都在 exe 旁邊。
if getattr(sys, 'frozen', False):
    _exe_dir = os.path.dirname(sys.executable)
    os.chdir(_exe_dir)
    # 同時將 exe 目錄加入 sys.path，讓 import 找得到根目錄模組
    if _exe_dir not in sys.path:
        sys.path.insert(0, _exe_dir)

# 必須明確 import 您的 FastAPI app 物件，打破字串依賴
from api.main import app

if __name__ == "__main__":
    # Windows 打包多進程必備防護
    multiprocessing.freeze_support()

    # 直接傳入 app 物件，嚴禁使用 "api.main:app" 字串
    uvicorn.run(app, host="0.0.0.0", port=8088)