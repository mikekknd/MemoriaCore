# Weather Cache 架構：SU 專用 Prompt 注入

## 目標

`weather_cache.json` 是對話 prompt 的背景環境快取，不是一般天氣查詢工具的共享快取。它只應服務 SU（SuperUser）的私域對話，避免其他使用者或臨時查詢城市污染常駐 prompt。

## 現行規則

- `weather_city` 視為 SU 的常駐城市設定。
- FastAPI 啟動預熱只在已設定 SU、`weather_city`、`openweather_api_key` 時執行。
- 對話 prompt 注入只在 `session_ctx.user_id == SU_USER_ID` 且 `persona_face == "private"` 時執行。
- 非 SU 或 public face 不讀取 `WeatherCache`，也不會把天氣摘要加到 user message 前綴。
- 若 SU private face 當輪需要天氣：
  1. 先讀 `WeatherCache().get_current_slot(weather_city)`。
  2. 若命中，直接注入，不呼叫 OpenWeather，也不產生命中 log。
  3. 若未命中且有 API key，才呼叫 `ensure_today(weather_city, api_key)` 刷新。
  4. 刷新後仍無資料則省略天氣區塊。

## 與 get_weather tool 的差異

`tools/weather.py` 的 `get_weather` 是使用者明確詢問天氣時的即時查詢工具，可查任意城市，不寫入 `weather_cache.json`。

`tools/weather_cache.py` 則是 prompt 背景注入資料，只應保存 SU 常駐城市。不要把一般使用者詢問過的城市寫入這份快取。

## 修改入口

- Prompt 注入邏輯：`core/prompt_utils.py`
- 單層對話傳入 context：`api/routers/chat/orchestration.py`
- 雙層對話傳入 context：`core/chat_orchestrator/coordinator.py`
- 啟動預熱：`api/main.py`

若未來加入多個 SU 或多地點日程，應先把 `weather_city` 升級成 per-SU profile 欄位，再擴充快取 key；不要回到全域多城市 prompt cache。
