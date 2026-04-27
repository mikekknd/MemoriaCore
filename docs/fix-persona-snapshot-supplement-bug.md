# Fix：PersonaSnapshot 補充邏輯導致的測試失敗

## 失敗清單

```
tests/test_persona_trait_store.py::TestVnUpdates::test_update_none_bumps_active_but_no_dim_row
tests/test_persona_trait_store.py::TestVnUpdates::test_update_to_nonexistent_trait_is_skipped
tests/test_persona_evolution_api.py::TestGetTree::test_orphan_parent_not_linked
```

三個測試皆為預先存在失敗（git stash 前後一致，非近期 PR 引入）。

---

## 根本原因

**檔案：`core/storage_manager.py`，方法：`_load_dimensions_for`（約第 1289 行）**

此方法除了讀取指定 snapshot 的 `persona_dimensions` 直接記錄外，還有一段「補充邏輯」（約第 1330 行開始）：

```python
# 補入此版 snapshot 沒有 dimension 記錄的歷史 trait
have_keys = {item["dimension_key"] for item in result}
cursor.execute(
    "SELECT t.trait_key, t.name, t.is_active, t.parent_key "
    "FROM persona_traits t "
    "WHERE t.character_id = ? AND t.persona_face = ? AND t.created_version <= ?",
    (character_id, persona_face, version),
)
missing = [r for r in cursor.fetchall() if r[0] not in have_keys]
```

設計目的：Force-Directed Graph 顯示時，即使某個 trait 這一版沒被更新，也要補入它的最近已知 confidence，讓圖完整。

**問題**：補充邏輯沒有區分以下兩種「這版沒有 dim row」的情況：

| 情況 | 說明 | 應該補入？ |
|---|---|---|
| 這版完全沒動到（未出現在 update 清單） | trait 在歷史版存在，這版未更新 | ✅ 應補入（圖要顯示） |
| 這版用 `confidence="none"` 明確處理 | 寫了 update 但刻意不寫 dim row | ❌ 不應補入歷史值（會覆蓋「無信心」的語義） |

`confidence="none"` 的 update 流程（`save_trait_snapshot`）：
- 寫 `UPDATE persona_traits SET last_active_version = current_version`（bump 了）
- **不**寫 `persona_dimensions` row
- 結果：`have_keys` 為空，補充邏輯把 V1 的值帶回來 → 錯誤

---

## 三個失敗的個別分析

### 失敗 1：`test_update_none_bumps_active_but_no_dim_row`

```python
# V1：建立 "依戀錨定"，confidence="high"
d1 = TraitDiff(new_traits=[_new("依戀錨定", "描述 A")])
store.save_snapshot(CHAR, d1, "v1", "p1")
tk = store.list_active_traits(CHAR)[0]["trait_key"]

# V2：用 confidence="none" 更新 → 不應寫 dim row
d2 = TraitDiff(updates=[_upd(tk, "none")])
store.save_snapshot(CHAR, d2, "v2", "p2")

v2 = storage.get_latest_persona_snapshot(CHAR)
assert len(v2["dimensions"]) == 0  # 預期：空
# 實際：1 筆（V1 的 "依戀錨定" confidence=8.0 被補進來）
```

**修法**：見下方「修正 `_load_dimensions_for`」。

---

### 失敗 2：`test_update_to_nonexistent_trait_is_skipped`

```python
# V1：建立 "依戀錨定"
d1 = TraitDiff(new_traits=[_new("依戀錨定", "描述 A")])
store.save_snapshot(CHAR, d1, "v1", "p1")

# V2：update 一個不存在的 key → snapshot_store 靜默略過
d2 = TraitDiff(updates=[_upd("nonexistent_key_1234", "high")])
store.save_snapshot(CHAR, d2, "v2", "p2")

v2 = storage.get_latest_persona_snapshot(CHAR)
assert len(v2["dimensions"]) == 0  # 預期：空
# 實際：1 筆（V1 的 "依戀錨定" 被補進來）
```

**分析**：這裡的補充**行為是正確的**。"依戀錨定" 在 V2 根本沒被觸及（nonexistent_key 在 `snapshot_store.save_snapshot` 中被靜默略過，`last_active_version` 未 bump），補入 V1 的值完全符合補充邏輯的設計意圖。

**修法**：修改測試斷言，不要斷言 `len == 0`，而是斷言「nonexistent_key_1234 沒有出現在 dimensions 中」：

```python
v2 = storage.get_latest_persona_snapshot(CHAR)
# nonexistent key 的 update 被略過，不會有對應 dim row
assert not any(d["dimension_key"] == "nonexistent_key_1234" for d in v2["dimensions"])
# "依戀錨定" 從 V1 補入是正確行為，trait 仍活躍
active = store.list_active_traits(CHAR)
assert len(active) == 1
assert active[0]["last_active_version"] == 1  # V2 沒動到它
```

---

### 失敗 3：`test_orphan_parent_not_linked`（`test_persona_evolution_api.py`）

```python
# V1：建立 root trait "依戀"
store.save_snapshot(CHAR, _diff_new([{"name": "依戀", ...}]), "s1", "p1")
root_key = store.list_active_traits(CHAR)[0]["trait_key"]

# V2：root 用 update=none（不寫 dim），新增 "孤兒子" 指向 root
td2 = TraitDiff(
    updates=[TraitUpdate(trait_key=root_key, confidence="none")],
    new_traits=[NewTrait(name="孤兒子", parent_key=root_key, confidence="high", ...)],
)
store.save_snapshot(CHAR, td2, "s2", "p2")

# GET /api/v1/system/personality/snapshots/2/tree
# 預期：nodes 只有 "孤兒子"，root 被 none 濾掉 → links 為空
names = [n["name"] for n in data["nodes"]]
assert names == ["孤兒子"]
assert data["links"] == []
# 實際：names == ["孤兒子", "依戀"]（root 被補回來了）
```

**修法**：同「修正 `_load_dimensions_for`」，root 被 `confidence=none` update bump 後不應被補回。

---

## 修正 `_load_dimensions_for`（解決失敗 1 和失敗 3）

**檔案**：`core/storage_manager.py`，方法 `_load_dimensions_for`，約第 1327 行起。

**現有程式碼（問題段落）：**

```python
if character_id is None or version is None:
    return result

# 補入此版 snapshot 沒有 dimension 記錄的歷史 trait
have_keys = {item["dimension_key"] for item in result}
if persona_face is not None:
    cursor.execute(
        "SELECT t.trait_key, t.name, t.is_active, t.parent_key "
        "FROM persona_traits t "
        "WHERE t.character_id = ? AND t.persona_face = ? AND t.created_version <= ?",
        (character_id, persona_face, version),
    )
else:
    cursor.execute(...)
missing = [r for r in cursor.fetchall() if r[0] not in have_keys]
```

**修正後（在 `missing` 計算前，加入排除 `confidence=none` 的 bump）：**

```python
if character_id is None or version is None:
    return result

have_keys = {item["dimension_key"] for item in result}

# 找出這版有 bump last_active_version 但沒有 dim row 的 trait
# （即 confidence="none" 的 update），這些不應被補入歷史值
if persona_face is not None:
    cursor.execute(
        "SELECT trait_key FROM persona_traits "
        "WHERE character_id = ? AND persona_face = ? AND last_active_version = ?",
        (character_id, persona_face, version),
    )
else:
    cursor.execute(
        "SELECT trait_key FROM persona_traits "
        "WHERE character_id = ? AND last_active_version = ?",
        (character_id, version),
    )
visited_none_keys = {r[0] for r in cursor.fetchall()} - have_keys
# visited_none_keys = 這版有 bump 但沒有 dim row → confidence=none 那批，不補

if persona_face is not None:
    cursor.execute(
        "SELECT t.trait_key, t.name, t.is_active, t.parent_key "
        "FROM persona_traits t "
        "WHERE t.character_id = ? AND t.persona_face = ? AND t.created_version <= ?",
        (character_id, persona_face, version),
    )
else:
    cursor.execute(
        "SELECT t.trait_key, t.name, t.is_active, t.parent_key "
        "FROM persona_traits t "
        "WHERE t.character_id = ? AND t.created_version <= ?",
        (character_id, version),
    )
missing = [
    r for r in cursor.fetchall()
    if r[0] not in have_keys and r[0] not in visited_none_keys
]
```

---

## 執行驗證

修完後跑：

```bash
python -m pytest tests/test_persona_trait_store.py::TestVnUpdates -v
python -m pytest tests/test_persona_evolution_api.py::TestGetTree::test_orphan_parent_not_linked -v
```

預期全部通過。完整 suite：

```bash
python -m pytest tests/ -q --ignore=tests/test_supplement_dimensions.py
```

預期：只剩 `test_supplement_dimensions.py` 被 ignore，其餘全過。

---

## 注意事項

- `_load_dimensions_for` 的 `else` 分支（`persona_face is None`）是向後相容路徑，兩個分支都要加對應的排除查詢（見上方修正）。
- 不要動 `test_supplement_dimensions.py`，那是另一組獨立問題。
- 修改只在 `core/storage_manager.py` 一個檔案，不影響其他模組。
