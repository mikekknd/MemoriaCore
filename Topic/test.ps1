# 強制設定 PowerShell 終端機與輸出/輸入編碼為 UTF-8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# 設定環境變數，確保 Node.js (Claude CLI 的底層) 也使用 UTF-8
$env:NODE_OPTIONS = "--use-openssl-ca"
$env:LANG = "zh_TW.UTF-8"

$WorkingDir = "C:\Users\USER\Downloads\Topic"
Set-Location -Path $WorkingDir


$Prompt = "上網查詢本季動畫新番相關消息，並將結果輸出為 report.md 存放在當前目錄。不要詢問確認，直接執行並覆寫檔案。"

claude -c $Prompt
