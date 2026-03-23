# ============================================================
# LLM Memory System — 打包腳本
# 執行方式：直接雙擊 build_server.bat，或在 PowerShell 中執行此檔案
# ============================================================
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# 切換到腳本所在目錄（確保相對路徑正確）
Set-Location $PSScriptRoot

# ──────────────────────────────────────────
# 輔助函式
# ──────────────────────────────────────────
function Write-Step($n, $total, $msg) {
    Write-Host ""
    Write-Host "[$n/$total] $msg" -ForegroundColor Yellow
}

function Write-OK($msg)   { Write-Host "  ✓ $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  [警告] $msg" -ForegroundColor DarkYellow }
function Write-Err($msg)  { Write-Host "  [錯誤] $msg" -ForegroundColor Red }

# ──────────────────────────────────────────
# 開始
# ──────────────────────────────────────────
Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " [LLM Memory System]  全自動打包流程"      -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# ──────────────────────────────────────────
# [1/5] 清除舊的編譯快取
# ──────────────────────────────────────────
Write-Step 1 5 "清除舊的編譯快取 (build, dist, spec)..."
if (Test-Path "build")          { Remove-Item -Recurse -Force "build";          Write-OK "build/ 已清除" }
if (Test-Path "dist")           { Remove-Item -Recurse -Force "dist";           Write-OK "dist/ 已清除" }
if (Test-Path "LLMServer.spec") { Remove-Item -Force "LLMServer.spec";          Write-OK "LLMServer.spec 已清除" }

# ──────────────────────────────────────────
# [2/5] 啟動虛擬環境並安裝必要依賴
# ──────────────────────────────────────────
Write-Step 2 5 "啟動虛擬環境 (venv_ai_memory) 並確認核心依賴..."

$venvActivate = "venv_ai_memory\Scripts\Activate.ps1"
if (-not (Test-Path $venvActivate)) {
    Write-Err "找不到虛擬環境：$venvActivate"
    Write-Err "請先執行 setup.bat 建立虛擬環境。"
    Read-Host "按 Enter 鍵離開"
    exit 1
}
& $venvActivate

Write-Host "  正在驗證 pyinstaller, uvicorn, fastapi, websockets 狀態..."
pip install pyinstaller uvicorn fastapi websockets -q
if ($LASTEXITCODE -ne 0) {
    Write-Err "pip install 失敗，請檢查網路或虛擬環境狀態。"
    Read-Host "按 Enter 鍵離開"
    exit 1
}
Write-OK "依賴驗證完成"

# ──────────────────────────────────────────
# [3/5] 執行 PyInstaller 打包
# ──────────────────────────────────────────
Write-Step 3 5 "執行 PyInstaller 打包..."
Write-Host "  ★ ONNX 模型不打包進 _internal/（避免路徑衝突），將在步驟 4 複製到 exe 旁邊" -ForegroundColor Gray

# 注意：使用陣列傳遞參數，PowerShell 會正確處理空白與引號
$pyArgs = @(
    "-m", "PyInstaller",
    "--name", "LLMServer",
    "--onedir",
    "--hidden-import=uvicorn",
    "--hidden-import=uvicorn.logging",
    "--hidden-import=uvicorn.loops",
    "--hidden-import=uvicorn.loops.auto",
    "--hidden-import=uvicorn.protocols",
    "--hidden-import=uvicorn.protocols.http",
    "--hidden-import=uvicorn.protocols.http.auto",
    "--hidden-import=uvicorn.protocols.websockets",
    "--hidden-import=uvicorn.protocols.websockets.auto",
    "--hidden-import=uvicorn.lifespan",
    "--hidden-import=uvicorn.lifespan.on",
    "--hidden-import=fastapi",
    "--hidden-import=websockets",
    "--hidden-import=onnxruntime",
    "--hidden-import=onnxruntime.capi",
    "--hidden-import=onnxruntime.capi._pybind_state",
    "--collect-all=onnxruntime",
    "run_server.py"
)

python @pyArgs

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Err "PyInstaller 打包失敗，請檢查上方錯誤訊息。"
    Read-Host "按 Enter 鍵離開"
    exit 1
}
Write-OK "PyInstaller 打包成功"

# ──────────────────────────────────────────
# [4/5] 複製執行期資料檔案到 dist\LLMServer\
# ──────────────────────────────────────────
Write-Step 4 5 "複製執行期資料檔案到 dist\LLMServer\ ..."

# --- ONNX 模型 ---
# llm_gateway.py 使用 glob.glob("StreamingAssets/Models/*.onnx")，
# 這是相對於 CWD 的路徑。exe 啟動後 CWD = exe 所在目錄，
# 因此 ONNX 必須放在 dist\LLMServer\StreamingAssets\Models\ 旁邊。
$onnxSrc  = "StreamingAssets\Models\*.onnx"
$onnxDest = "dist\LLMServer\StreamingAssets\Models"
if (-not (Test-Path $onnxDest)) {
    New-Item -ItemType Directory -Force -Path $onnxDest | Out-Null
}
$onnxFiles = Get-Item $onnxSrc -ErrorAction SilentlyContinue
if ($onnxFiles) {
    Copy-Item $onnxSrc $onnxDest -Force
    Write-OK "ONNX 模型已複製 → $onnxDest"
} else {
    Write-Warn "找不到 $onnxSrc，請確認模型檔案存在。"
}

# --- user_prefs.json（模型路由設定）---
if (Test-Path "user_prefs.json") {
    Copy-Item "user_prefs.json" "dist\LLMServer\" -Force
    Write-OK "user_prefs.json 已複製（包含模型路由設定）"
} else {
    Write-Warn "user_prefs.json 不存在，略過。exe 首次啟動將使用內建預設值。"
}

# --- system_prompt.txt（系統提示詞）---
if (Test-Path "system_prompt.txt") {
    Copy-Item "system_prompt.txt" "dist\LLMServer\" -Force
    Write-OK "system_prompt.txt 已複製"
} else {
    Write-Warn "system_prompt.txt 不存在，略過。"
}

# ──────────────────────────────────────────
# [5/5] 完成
# ──────────────────────────────────────────
Write-Host ""
Write-Host "[5/5] 打包流程結束！" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "產出位置：dist\LLMServer\"
Write-Host ""
Write-Host "  dist\LLMServer\"
Write-Host "  ├── LLMServer.exe"
Write-Host "  ├── user_prefs.json         (模型路由設定)"
Write-Host "  ├── system_prompt.txt       (系統提示詞)"
Write-Host "  ├── StreamingAssets\"
Write-Host "  │   └── Models\"
Write-Host "  │       └── *.onnx          (BGE-M3 向量模型)"
Write-Host "  └── _internal\              (Python 執行環境)"
Write-Host ""
Write-Host "請將整個 dist\LLMServer\ 資料夾複製到 Unity 的"
Write-Host "  StreamingAssets\LLMServer\  並覆蓋舊檔。"
Write-Host "==========================================" -ForegroundColor Cyan
Read-Host "按 Enter 鍵離開"
