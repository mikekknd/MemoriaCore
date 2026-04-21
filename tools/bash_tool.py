import re
import subprocess
import shlex
import json
import platform
import locale
from core.system_logger import SystemLogger

# 拒絕包含 shell 指令串接符的輸入（allowlist 模式下）
# 允許：| （管線）、> < （重導向，touch 翻譯需要）
# 禁止：; && || ` $( — 可用於串接任意命令繞過 allowlist
_INJECTION_RE = re.compile(r'(?:&&|\|\||;|`|\$\()')

_CURRENT_OS = platform.system()
_IS_WINDOWS = _CURRENT_OS == "Windows"

# ════════════════════════════════════════════════════════════
# SECTION: 平台相關常數（動態偵測）
# ════════════════════════════════════════════════════════════
if _IS_WINDOWS:
    _OS_HINT = (
        "【⚠️ Windows 環境】生成命令時請直接使用 Windows 等價指令：\n"
        "  cat → type | ls → dir | grep → findstr | rm → del /f | mv → move | cp → copy\n"
        "  touch → type nul > | pwd → cd | find → dir /s /b | sort → sort /r\n"
        "  wc → findstr /n | head/tail → more +1 | diff → fc | ps → tasklist | df → wmic\n"
        "  free → systeminfo | uptime → net statistics workstation | env/set → set\n"
        "  sleep → timeout /t | uname → ver | mkdir/rmdir/curl/wget 等直接可用。\n"
        "【禁止】不可使用 Unix 特有指令，否則會執行失敗。\n"
        "【範例】讀取檔案：type filename（不是 cat filename）| 列出檔案：dir（不是 ls）"
    )
else:
    _OS_HINT = (
        "【⚠️ Unix/Linux/macOS 環境】生成命令時請直接使用原生 Unix 指令。\n"
        "【範例】讀取檔案：cat filename | 列出檔案：ls -la"
    )

# 預設群組（供 UI 使用的參考分類，不影響執行邏輯）
PRESET_GROUPS = [
    ("📁 檔案瀏覽",  ["ls", "dir"]),
    ("📄 檔案讀取",  ["cat", "type"]),
    ("🔧 Git 操作",  ["git"]),
    ("🐍 執行 Python", ["python", "python3"]),
    ("🌐 網路診斷",  ["ping", "curl", "wget"]),
    ("⚙️ 系統資訊",  ["ps", "df", "free", "uname"]),
    ("📦 套件管理",  ["npm", "pip", "cargo"]),
    ("🧮 基礎工具",  ["echo", "date", "whoami", "pwd", "hostname", "uptime", "env", "printenv"]),
    ("📂 基礎檔案",  ["mkdir", "touch", "cp", "mv", "rm", "rmdir", "cd", "cwd"]),
    ("🔢 計算工具",  ["expr", "bc", "seq"]),
]

BASH_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_bash",
        "description": (
            "【功能】在本機執行 shell 指令並回傳輸出。\n"
            "【觸發時機】本機檔案操作（建立、讀取、修改、刪除）、執行腳本、查詢系統狀態、git 操作等。\n"
            "【不適用】需要開啟瀏覽器或操作網頁的任務，請改用 browser_task。\n"
            + _OS_HINT
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要執行的 shell 指令（例如：dir /a, type test.md, git log --oneline -5）",
                }
            },
            "required": ["command"],
        },
    },
}


def _load_prefs() -> dict:
    try:
        from core.storage_manager import StorageManager
        return StorageManager().load_prefs()
    except Exception:
        return {}


def run_bash(command: str) -> str:
    prefs = _load_prefs()
    allow_all = prefs.get("bash_tool_allow_all", False)
    allowed = [c.strip().lower() for c in prefs.get("bash_tool_allowed_commands", []) if c.strip()]

    if not allow_all and not allowed:
        return json.dumps({"error": "Bash Tool 尚未設定允許指令清單，請至設定頁啟用並勾選允許的指令。"}, ensure_ascii=False)

    # Windows 原生命令 → 對應 Unix 別名（用於 allowlist 反向查詢）
    # 讓 LLM 直接使用 Windows 命令時，仍能匹配以 Unix 名稱登記的 allowlist
    _WIN_TO_UNIX_ALIAS = {
        "del": "rm",
        "erase": "rm",
        "type": "cat",
        "dir": "ls",
        "move": "mv",
        "copy": "cp",
        "cls": "clear",
        "where": "which",
        "findstr": "grep",
        "tasklist": "ps",
        "taskkill": "kill",
        "ver": "uname",
        "timeout": "sleep",
    }

    try:
        if not allow_all:
            # 先攔截 shell 注入串接符（; && || ` $()），再做 allowlist 檢查
            if _INJECTION_RE.search(command):
                return json.dumps(
                    {"error": "指令包含不允許的 shell 串接符（; && || ` $()），已拒絕執行。"},
                    ensure_ascii=False,
                )

            parts = shlex.split(command, posix=False)
            base_cmd = parts[0].lower() if parts else ""
            # 移除可能的路徑前綴（如 /usr/bin/git → git）
            base_cmd = base_cmd.replace("\\", "/").split("/")[-1]
            # 移除 .exe 後綴（Windows）
            if base_cmd.endswith(".exe"):
                base_cmd = base_cmd[:-4]

            # 查 allowlist 時同時查 Windows 別名（如 move → mv）
            unix_alias = _WIN_TO_UNIX_ALIAS.get(base_cmd, base_cmd)
            if base_cmd not in allowed and unix_alias not in allowed:
                return json.dumps(
                    {"error": f"指令 '{base_cmd}' 不在允許清單內（目前允許：{', '.join(allowed)}）"},
                    ensure_ascii=False,
                )

        # Unix → Windows 命令轉寫（shell=True 時 Windows 不認得 Unix 指令）
        _UNIX_TO_WINDOWS = {
            "cat": "type",
            "ls": "dir",
            "ll": "dir /a" if _IS_WINDOWS else "ll",
            "grep": "findstr" if _IS_WINDOWS else "grep",
            "which": "where" if _IS_WINDOWS else "which",
            "find": "dir /s /b" if _IS_WINDOWS else "find",
            "rm": "del /f" if _IS_WINDOWS else "rm",
            "rmdir": "rmdir /s /q" if _IS_WINDOWS else "rmdir",
            "mv": "move" if _IS_WINDOWS else "mv",
            "cp": "copy" if _IS_WINDOWS else "cp",
            "pwd": "cd" if _IS_WINDOWS else "pwd",
            "clear": "cls" if _IS_WINDOWS else "clear",
            "touch": "type nul >" if _IS_WINDOWS else "touch",
            "head": "powershell -c \"Get-Content\" " if _IS_WINDOWS else "head",
            "tail": "powershell -c \"Get-Content\" " if _IS_WINDOWS else "tail",
            "diff": "fc" if _IS_WINDOWS else "diff",
            "sort": "sort" if _IS_WINDOWS else "sort",
            "wc": "findstr /n /r \".\"" if _IS_WINDOWS else "wc",
            "date": "date /t" if _IS_WINDOWS else "date",
            "sleep": "timeout /t" if _IS_WINDOWS else "sleep",
            "uname": "ver" if _IS_WINDOWS else "uname",
            "ps": "tasklist" if _IS_WINDOWS else "ps",
            "kill": "taskkill /f /pid" if _IS_WINDOWS else "kill",
            "df": "wmic logicaldisk get size,freespace,caption" if _IS_WINDOWS else "df",
            "free": "systeminfo | findstr /i \"memory\"" if _IS_WINDOWS else "free",
            "uptime": "net statistics workstation" if _IS_WINDOWS else "uptime",
            "env": "set" if _IS_WINDOWS else "env",
            "printenv": "set" if _IS_WINDOWS else "printenv",
            "hostname": "hostname" if _IS_WINDOWS else "hostname",
            "mkdir": "mkdir" if _IS_WINDOWS else "mkdir",
            "curl": "curl" if _IS_WINDOWS else "curl",
            "wget": "curl -O" if _IS_WINDOWS else "wget",
            "seq": "powershell -c \"1..\" " if _IS_WINDOWS else "seq",
        }

        translated_cmd = command
        for unix_cmd, win_cmd in _UNIX_TO_WINDOWS.items():
            if parts and parts[0].lower() == unix_cmd:
                parts = list(parts)
                # rm：若含 -r/-rf 旗標則刪目錄，否則刪檔案
                if unix_cmd == "rm" and _IS_WINDOWS:
                    flags = [p for p in parts[1:] if p.startswith("-")]
                    targets = [p for p in parts[1:] if not p.startswith("-")]
                    if any("r" in f for f in flags):
                        translated_cmd = "rmdir /s /q " + " ".join(targets)
                    else:
                        translated_cmd = "del /f " + " ".join(targets)
                else:
                    parts[0] = win_cmd
                    translated_cmd = " ".join(parts)
                break

        # Windows cmd.exe 使用系統 codepage（CP950/Big5 等），不是 UTF-8
        # 強制用 UTF-8 會導致中文路徑/檔名傳遞失敗
        _encoding = locale.getpreferredencoding(False) if _IS_WINDOWS else "utf-8"
        result = subprocess.run(
            translated_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=15,
            encoding=_encoding,
            errors="replace",
        )
        output = result.stdout or result.stderr or ""
        # echo / type nul 重導向在 Windows 成功時無輸出，視為成功
        _redirect_ok = (
            translated_cmd.startswith("type nul >")
            or (base_cmd == "echo" and ">" in command)
        ) and not output and result.returncode == 0
        if _redirect_ok:
            # 檔名固定是最後一個 token
            created_file = parts[-1] if parts else "該檔案"
            output = f"（已成功建立：{created_file}）"
        elif not output:
            output = "（無輸出）"
        if len(output) > 3000:
            output = output[:3000] + "\n…（輸出過長，已截斷）"
        return json.dumps({"output": output}, ensure_ascii=False)

    except subprocess.TimeoutExpired:
        return json.dumps({"error": "指令執行逾時（15 秒限制）"}, ensure_ascii=False)
    except Exception as e:
        SystemLogger.log_error("BashTool", str(e))
        return json.dumps({"error": str(e)}, ensure_ascii=False)
