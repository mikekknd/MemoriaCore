# YouTubeBridge 直播底層規則

這份文件記錄 dashboard 欄位背後的實際運作規則。dashboard 的「規則說明」頁會直接讀取本文件，因此更新規則時應同步更新這裡。

## 角色選擇規則

### 角色數量上限

- Live Session 會從 MemoriaCore `/system/config` 讀取 `max_session_characters`，並以此限制 dashboard 可選角色數。
- 目前 MemoriaCore 群聊上限為 6 位角色。
- 超過上限時 dashboard 會停用尚未選取的角色選項，避免送出後才由後端拒絕。

### 未選角色不可開始

- 開始直播前至少要選擇 1 位角色。
- 沒有角色時 dashboard 會停用「開始直播」按鈕。
- 如果直播已經在執行中，即使角色清單狀態異常，仍允許使用同一顆按鈕進入收尾流程，避免直播卡住無法結束。

## 留言注入節奏

### 注入間隔秒數

- 這是自動注入 loop 的基礎檢查週期。
- 它不是普通留言專用；普通留言與 Super Chat 都會先進入 pending event，再由同一個注入 loop 挑選。
- 若啟用自動注入，系統每輪會檢查目前 pending 留言、是否有角色正在回應、是否達到最少 pending 留言門檻，以及是否有 Super Chat。
- 若目前有角色正在回應，loop 仍會檢查，但通常不會插入一般留言，除非 backlog 已達強制注入上限，或 Super Chat 符合打斷規則。

### 動態注入最短秒數

- 這是注入間隔可被縮短到的最低秒數。
- 當沒有角色正在回應時，pending 留言越接近「pending 強制注入上限」，系統越會把下一輪等待時間從「注入間隔秒數」往這個最低秒數靠近。
- 當有角色正在回應時，系統會回到基礎「注入間隔秒數」，避免一邊回應一邊過度加速。

### 最少 pending 留言

- pending 留言數達到此值後，自動注入才會把一般留言送進角色回應流程。
- Super Chat 可優先觸發，不一定要等一般留言數達到這個門檻。

### pending 強制注入上限

- 這是單次自動注入最多帶入的 pending 留言數，也是 backlog 壓力判斷的上限。
- 若角色正在回應，但 pending backlog 已達這個上限，系統可以排入下一輪，避免留言長時間堵住。

## Super Chat 規則

### SC 打斷冷卻秒數

- 這個欄位控制的是「Super Chat 連續打斷正在進行的角色回應」的冷卻時間。
- 它不是 Super Chat 的固定注入週期，也不是「每 N 秒一定處理一則 SC」。
- 若沒有角色正在回應，Super Chat 會在下一次自動注入檢查時被優先處理，不需要等待這個冷卻。
- 若角色正在回應，只有距離上一次 Super Chat 打斷已超過此秒數，新的 Super Chat 才能打斷目前回應。
- 若冷卻尚未滿足，Super Chat 會留在 pending，等待後續注入時機。

### 每批 SC 上限

- 每次注入最多帶入幾則 Super Chat。
- 系統會優先選 Super Chat，再選一般留言。
- Super Chat 會先依 tier 由高到低排序，同 tier 再依收到順序處理。

### 收尾時逐一感謝未處理 SC

- 直播進入收尾時，系統會讀取尚未處理的 Super Chat。
- 感謝方式偏向片尾名單：逐一點名或分組感謝支持者。
- 可疑或不適合公開的 Super Chat 不應逐字重述攻擊內容。

## 導播規則

### 導播回合上限

- 導播每次推進話題後，允許角色連續互相接話的最大回合數。
- 這不是整場直播的總回合數，只限制單次導播指令能延伸多久。
- 目的是避免一個導播提示讓角色無限延伸，導致直播節奏失控。

### 幾批留言後回主軸

- 每完成一批非導播來源的聊天室留言注入後，系統會累計 chat batch。
- 累計達到這個數字後，導播會優先把話題拉回本場直播主軸。
- 這個規則用來避免直播長時間被留言帶偏。

### 角色停頓後續話秒數

- 當沒有 running 或 queued interaction，且沒有需要先處理的注入內容時，導播會開始計算 idle。
- idle 超過設定秒數後，導播會嘗試推進下一段話題或讓角色續話。
- 如果仍有 pending 留言、Super Chat 或正在執行的 interaction，idle 推進可能會被延後。

### 本場直播方向

- 這是導播與角色看的內部方向，不會直接顯示在 live chat。
- 它應描述本場直播主題、角色互動風格、避免事項與主軸收束方向。

### 主持互動規則

- 這是 Live Session 層級的節目主持結構，不屬於任何單一角色 persona。
- 可放入雙主持分工、接話節奏、避免互相附和、不同觀點輪替等規則。
- 內容只會透過 trusted external context 提供給導播、群聊 router 與角色接力 prompt，不會顯示在 live chat，也不寫回 MemoriaCore 角色設定或 shared memory。

### 節目段落流程

- 每行代表一個討論段落，例如事件 Hook、觀眾驚訝點、核心分析、反方觀點、收束金句。
- 導播會根據「每段落建議回合數」在段落間推進，避免同一觀點一直重複。
- 新直播、開場或 topic transition 會從第一個段落重新開始；沒有填段落流程時，不會注入段落狀態。

## Research Gate 與 Fact Cards

### Fact Cards

- Fact Cards 是預先準備的話題資料庫。
- 角色回應時可以透過向量檢索召回相關內容，避免只聊表面話題。
- Fact Card raw markdown、embedding、hidden context 不應顯示到 live chat。

### Research Gate

- Research Gate 的主要用途是處理觀眾提出的資料型問題，而不是取代手動準備資料。
- 若觀眾問題可由 Fact Cards 命中，系統優先使用現有資料。
- 若資料包不足，系統會經過安全判定後補充外部搜尋上下文，再把處理後的摘要和觀眾留言一起交給角色。
- 外部搜尋應是降級補充，不應阻塞導播、Super Chat 感謝或 live chat 主流程。

## 測試留言與正式直播

### 測試留言

- 測試留言只寫入 YouTubeBridge 測試聊天室，不會送到 YouTube 平台。
- 它用來驗證安全處理、角色回應、Super Chat、惡意內容與長時間 E2E 流程。

### 正式直播

- 當填入 YouTube video_id 或 URL 並連到真實直播聊天室時，測試留言功能應被停用或灰階。
- 正式直播不應自動生成測試留言，避免額外 LLM 開銷與污染真實流程。

## 公開顯示邊界

- live chat 只顯示公開可見的直播對話、乾淨摘要與安全處理後內容。
- hidden context、完整導播 prompt、攻擊原文、Topic Pack raw content、Fact Card markdown 原文與 embedding 不應顯示到 live chat。
- 導播節奏提示只給角色看，不應成為觀眾可見 system event。
