# YouTubeBridge Studio UI Design

## 目標

建立一個新的 YouTubeBridge 直播頁面原型，作為舊 `/ui/` 控制台之外的乾淨操作介面。第一版只確定外觀與本地 mock 互動，不連接真實 LiveSession API。

## 核心方向

- 新頁面使用單一 `/studio/` 路由，舊 `/ui/`、`/live/`、`/live-chat/` 不改。
- 主畫面固定為三欄：左欄直播中控台，中欄直播對話，右欄 Debug 與測試。
- 左欄保留 LiveSession 相關主流程：企劃選擇、直播狀態、直播角色設定、開始直播、收尾/停止直播。
- 中欄只顯示直播對話，不顯示企劃流程看板、Topic Pack、圖表或健康監控牆。
- 右欄收納 Debug、留言測試、Summary，讓測試功能不混入主直播操作。

## 明確排除

新版主介面不得出現以下舊版功能或詞彙：

- Topic Pack
- legacy autonomous director
- program segment plan
- raw context
- 舊版手動選角色流程

角色設定要保留，但只作為直播角色設定面板，不作為舊版導播 fallback。

## 視覺規格

- 風格：乾淨的直播導播台，白色與冷灰背景、深色文字、細邊框、低陰影。
- 色彩：主色使用 teal/cyan；警示或測試狀態使用 amber；避免紫色漸層、米色、裝飾光球。
- 版面：桌面三欄比例約為左 300px、中間彈性最大、右 340px。窄螢幕可改為單欄堆疊。
- 元件：圓角 6-8px，按鈕與 tabs 使用少量 outline icon，文字簡短。
- 內容密度：比原本 `/ui/` 更少欄位，只保留第一版操作會用到的資訊。

## 第一版互動

- 企劃 dropdown 使用 mock 資料。
- 「開始直播」切換左欄狀態、中欄直播狀態，並追加幾則 mock 對話。
- 「收尾 / 停止直播」切換為已收尾狀態。
- 角色啟用 toggles 可在本頁切換。
- 右欄 `Debug / 留言測試 / Summary` tabs 可切換；留言測試送出後只在本頁顯示 mock 結果。

## 檔案邊界

- 新增 `YouTubeBridge/static/studio.html`。
- 新增 `YouTubeBridge/static/ui/studio.css`。
- 新增 `YouTubeBridge/static/ui/studio.js`。
- 修改 `YouTubeBridge/server_routes/ui.py` 與 `YouTubeBridge/server.py` 掛載 `/studio/`。
- 修改 `YouTubeBridge/server_security.py`，讓 `/studio/` 跟既有 UI 一樣只允許 loopback。
- 新增 route/static smoke test，避免新頁回歸混入 legacy 功能。

## 驗收

- `/studio/` 可被 FastAPI route 服務。
- `studio.html` 只引用外部 CSS/JS，不使用 inline style/script。
- 新頁主畫面有三個主要 panel：直播中控台、直播對話、Debug 與測試。
- 新頁不含 Topic Pack、program segment、autonomous director、raw context 等 legacy 文案。
- 靜態 UI 在瀏覽器桌面與窄螢幕檢查後不應有文字重疊或主要內容裁切。
