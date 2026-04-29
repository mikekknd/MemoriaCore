# 主動話題架構：Global Topic Pool 與角色領取

## 背景

背景話題蒐集的目的，是讓系統能根據使用者 profile 自動整理可自然提起的話題，未來可進一步用於「AI 主動向使用者發起對話」。

使用者 profile 是 user-level 資料，只依 `user_id` 與 `visibility` 隔離，不依 `character_id` 隔離。所有角色面對同一位使用者時，應讀到同一份使用者偏好與基本事實。因此，背景蒐集產生的話題不應綁定當下 UI 的 active character。

## 問題

舊設計中，背景蒐集會：

1. 讀取第一位 admin 的 private profile。
2. 根據 profile 搜尋最新資訊並摘要。
3. 將結果寫入 `topic_cache(user_id=admin_id, character_id=active_character_id, visibility='private')`。

這會讓使用者層級的話題被錯誤掛到某個角色底下。若背景蒐集時 active character 是 `char-a`，後續 `char-b` 即使面對同一位使用者，也讀不到這筆背景話題。

## 現行規則

背景蒐集產生的 topic 一律寫入 global pool：

```text
topic_cache.user_id = admin_user_id
topic_cache.character_id = "__global__"
topic_cache.visibility = "private"
```

`"__global__"` 表示這筆 topic 屬於使用者層級，不屬於任何單一角色。

聊天時，角色查詢 proactive topic 會同時讀取：

```text
character_id = current_character_id
OR
character_id = "__global__"
```

排序上角色專屬 topic 優先，global topic 次之；被注入 prompt 後會標記 `is_mentioned_to_user = 1`，避免重複提起。

## 為什麼採用 Global Topic Pool

這個設計保留三個需求：

1. **使用者 profile 共用**：背景 topic 來源是 user profile，不被 active character 影響。
2. **角色專屬擴充仍存在**：未來仍可建立 `character_id = "char-a"` 的角色專屬 topic。
3. **主動對話可擴充**：未來要讓 AI 主動發話時，可以先從 `__global__` pool 取 topic，再決定由哪個角色開口。

不建議把 topic_cache 完全改成只看 `user_id + visibility`，因為未來主動對話需要回答「誰來說」以及「是否已有角色領取」這兩件事。保留 `character_id` 欄位並用 `__global__` 作為 user-level pool，可以避免後續再做資料模型遷移。

## 未來擴充方向

主動對話功能可在現有模型上新增以下欄位或旁表：

```text
source_scope = "global" | "character"
claimed_by_character_id = nullable
claimed_at = nullable
trigger_policy = nullable
```

預期流程：

1. 背景蒐集將候選話題寫入 `character_id="__global__"`。
2. 主動對話排程器挑選一筆未提過的 global topic。
3. 發話角色選擇器根據角色狀態、使用者偏好、最近互動、群組設定選出 `claimed_by_character_id`。
4. 由該角色生成開場訊息。
5. 成功送出後標記 `is_mentioned_to_user=1`。

## 修改注意事項

- 背景蒐集 scope 在 `core/background_gatherer.py`，不得再使用 `active_character_id` 作為寫入目標。
- `GLOBAL_TOPIC_CHARACTER_ID` 定義於 `core/storage_manager.py`。
- topic 查詢邏輯在 `StorageManager.get_unmentioned_topics(..., include_global=True)`。
- prompt 注入入口在 `CoreMemorySystem.get_proactive_topics_prompt()`。
- 若未來新增主動推播 API，請沿用 `__global__` pool，並在送出時標記已提及，避免多角色重複提同一話題。
