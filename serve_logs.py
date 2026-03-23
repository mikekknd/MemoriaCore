"""
Log Viewer 快速啟動伺服器
執行方式：python serve_logs.py
會自動開啟瀏覽器並載入 llm_trace.jsonl
"""
import http.server
import socketserver
import webbrowser
import threading
import os
import sys

PORT = 8765
VIEWER = "log_viewer.html"

# 切換工作目錄到此腳本所在位置
os.chdir(os.path.dirname(os.path.abspath(__file__)))

if not os.path.exists(VIEWER):
    print(f"[錯誤] 找不到 {VIEWER}，請確認檔案存在。")
    sys.exit(1)


class SilentHandler(http.server.SimpleHTTPRequestHandler):
    """抑制 access log 輸出"""
    def log_message(self, format, *args):
        pass


def open_browser():
    webbrowser.open(f"http://localhost:{PORT}/{VIEWER}")


print(f"[Log Viewer] 啟動伺服器於 http://localhost:{PORT}/{VIEWER}")
print("[Log Viewer] 按 Ctrl+C 停止伺服器")

with socketserver.TCPServer(("", PORT), SilentHandler) as httpd:
    httpd.allow_reuse_address = True
    threading.Timer(0.5, open_browser).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[Log Viewer] 伺服器已停止。")
