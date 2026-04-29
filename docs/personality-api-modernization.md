# 人格 API 現代化：去除 active character 依賴

## 目標

MemoriaCore 已支援多角色單聊與群組聊天，對話、人格演化與 PersonaProbe 同步都應以明確的 `character_id` 為目標。`active_character_id` 僅保留為「未指定角色時的預設角色」，不應再作為人格管理或 PersonaProbe 同步的隱性目標。

## 舊設計問題

早期系統只有一個 active AI，因此提供 root endpoint：

```text
GET /system/personality
PUT /system/personality
```

這兩個端點沒有 `character_id` 參數，固定讀寫 `prefs.active_character_id` 的 `evolved_prompt`。在多角色架構下，這會造成幾個問題：

1. 使用者在 UI 指定聊天角色，但人格管理可能仍改到 active/default 角色。
2. PersonaProbe 同步目標不夠明確，容易被全域設定影響。
3. active character 的語意混雜：同時像是聊天目標、人格管理目標、預設 fallback。

## 現行規則

舊 root endpoint 已移除：

```text
GET /system/personality       # removed
PUT /system/personality       # removed
```

PersonaSync API 必須明確指定角色：

```text
GET  /system/personality/sync-status?character_id=<id>&persona_face=public
POST /system/personality/sync-now?character_id=<id>&persona_face=public
```

若缺少 `character_id`，API 回傳 422，不再 fallback 到 `active_character_id`。

## 保留的新人格演化 API

以下 API 屬於 Path D / trait tree 新系統，應保留：

```text
GET /system/personality/snapshots?character_id=<id>
GET /system/personality/snapshots/latest?character_id=<id>
GET /system/personality/snapshots/latest/tree?character_id=<id>
GET /system/personality/snapshots/{version}?character_id=<id>
GET /system/personality/snapshots/{version}/tree?character_id=<id>
GET /system/personality/traits?character_id=<id>
GET /system/personality/traits/timeline?character_id=<id>&trait_key=<key>
```

這些端點雖然仍掛在 `/system/personality` prefix 下，但都需要 `character_id`，不依賴 active character。`static/persona_tree.html` 使用的就是這批 API。

## Streamlit UI 調整

`ui/character.py` 的 PersonaProbe tab 不再透過寫入 `active_character_id` 來切換同步目標，而是使用頁面內部 state：

```text
st.session_state["probe_target_character_id"]
```

呼叫 sync API 時直接傳入該角色 ID。

角色列表中的 `active_character_id` 文案改為「預設角色」，表示它只在未指定角色時作為 fallback。

## 未來擴充方向

若需要手動編輯特定角色的 evolved prompt，應新增明確角色 API，例如：

```text
GET /character/{character_id}/personality
PUT /character/{character_id}/personality
```

或直接沿用現有 `/character` upsert API 編輯 `evolved_prompt`。不要恢復沒有 `character_id` 的 `/system/personality` root endpoint。

若未來重命名 API prefix，可考慮把 Path D API 搬到：

```text
/system/persona-evolution/...
```

但這是命名整理，不是功能必要條件。重命名時需同步更新 `static/persona_tree.html` 與 `tests/test_persona_evolution_api.py`。

## 修改注意事項

- `active_character_id` 仍可留在 `/system/config`，但語意是 default character。
- 新增 PersonaProbe 或人格演化入口時，必須要求或明確選取 `character_id`。
- 自動 PersonaSync 使用 conversation DB 推導出的 dirty set：掃描所有曾有 assistant 發言的角色，逐角色、逐 face 檢查觸發條件；完全沒有候選角色時不使用 default/active character 補位。
- `insufficient_messages(...)` 是正常等待狀態，不寫 `persona_sync_skip` log，避免每 20 分鐘刷出無行動價值的紀錄。
- 不要把人格管理目標綁到 `active_character_id`。
