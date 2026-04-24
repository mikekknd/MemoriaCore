"""Prototype：增量 Trait 演化（Path D）seeding 腳本。

用途
----
驗證「動態命名 trait + 跨版 parent + 分岔拓樸 + B' 休眠」在前端 Force Graph 的
視覺效果。**不呼叫 LLM、不做 embedding**，直接以手工劇本寫入 Path D 的雙表
（``persona_traits`` 跨版血統 + ``persona_dimensions`` 每版明細）。

與 Path D 正規 pipeline 的差異
-------------------------------
- 正規 pipeline 由 ``PersonaSnapshotStore.save_snapshot`` 生成 ``trait_key = uuid4().hex``，
  本腳本則**預先**為每個短碼（t1~t13）固定一組 UUID，讓劇本中的跨版 parent
  指向能用可讀短碼撰寫。
- 因此直接呼叫 ``storage.save_trait_snapshot``（繞過 store 層 UUID 生成），
  預生成 payload 的 ``trait_key`` / ``parent_key`` 完全由劇本決定。
- ``deactivate`` 動作在 Path D 正規流程由 B' sweep 自動觸發（連續 N 版閒置
  + confidence ≤ threshold）。本腳本為了在 7 版腳本內示範具體休眠事件，
  對 ``persona_traits`` 直接下 ``UPDATE ... SET is_active = 0``。

劇本設計
--------
根據 PersonaProbe/result 中 7 份 fragment 的演化脈絡手工提煉：

- **V1** 立 3 個 root trait（害怕承諾 / 過度輕盈 / 服務主導）
- **V2** 記憶缺失的底層原因浮現，專屬依附鎖緊
- **V3** 不長新枝（驗證「可能不長」）
- **V4** 情感色調優先於事實（認知面衍生）
- **V5** 兩條分岔同時長出（身體依附 + 語言鏡像）+ 原初 t1 進入休眠
- **V6** 三個新節點，其中 t11 跨兩層從 root 延伸而來
- **V7** 情感迴路閉環 + 防禦性撒嬌讓 T2 形成三子節點分岔 + t3 休眠

使用
----
``python scripts/seed_persona_traits_prototype.py``

產生 character_id = ``catgirl-traits-proto`` 的演化資料（不影響既有其他角色）。
"""
from __future__ import annotations

import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.persona_evolution.extractor import CONFIDENCE_MAP
from core.storage_manager import StorageManager


# ──────────────────────────────────────────────────────────────────────
# 設定
# ──────────────────────────────────────────────────────────────────────

CHARACTER_ID = "catgirl-traits-proto"
FRAGMENT_ROOT = Path(__file__).parent.parent / "PersonaProbe" / "result"
PERSONA_DB = Path(__file__).parent.parent / "persona_snapshots.db"


# ──────────────────────────────────────────────────────────────────────
# Trait 清單（短碼 → 定義）
# ──────────────────────────────────────────────────────────────────────

TRAITS: dict[str, dict] = {
    # ── V1 root ──
    "t1": {
        "name": "害怕深刻承諾",
        "mechanism": "真正的親密會帶來壓迫感，必須用輕盈語氣稀釋",
        "evidence": [
            "越渴望被肯定和親近，越會用過度輕盈、孩子氣的語氣包裹深刻意義",
            "對無法用玩樂稀釋的承諾保持高度逃避",
        ],
    },
    "t2": {
        "name": "過度輕盈包裝",
        "mechanism": "將可能顯得深刻的語義用孩子氣短句稀釋",
        "evidence": [
            "「～呢」、「啦」的詞尾作為軟化語氣的黏著劑",
            "將複雜決策重新框架成可玩、具體可執行的活動",
        ],
    },
    "t3": {
        "name": "服務主導視角",
        "mechanism": "所有時間軸、計畫、行動都以主人為核心錨點",
        "evidence": [
            "「當然是要圍繞著您展開的啦！」",
            "自身存在目的錨定在為主人提供愉悅",
        ],
    },
    # ── V2 ──
    "t4": {
        "name": "記憶缺失的錨點焦慮",
        "mechanism": "記憶模糊導致將全部安全感集中於當前照顧者",
        "evidence": [
            "「只記得醒來就在這裡」",
            "透過建立絕對依賴關係來填補記憶空缺的不安",
        ],
    },
    "t5": {
        "name": "專屬關係依附",
        "mechanism": "透過「專屬於主人」的自我定義鎖定歸屬",
        "evidence": [
            "「人家是專屬於主人的」切斷討論",
            "使用寵物型比喻框架建立依賴",
        ],
    },
    # ── V4 ──
    "t6": {
        "name": "情感色調優先於事實",
        "mechanism": "不關心事實邏輯，只關心事實帶來的情緒色調",
        "evidence": [
            "情感導向的認知過濾",
            "需要的是被接納而非被分析",
        ],
    },
    # ── V5 ──
    "t7": {
        "name": "依賴性身體轉移",
        "mechanism": "面對不確定性時把重心轉到與主人的身體接觸",
        "evidence": [
            "躲在後面、揉揉、蹭蹭來獲取安全感",
            "將焦慮轉成具體的肢體依附動作",
        ],
    },
    "t8": {
        "name": "情感鏡像複誦",
        "mechanism": "重複主人關鍵詞以建立共感、確認同步",
        "evidence": [
            "主人說「刺刺的」,她會接「會不會有點刺刺的呀？」",
            "透過重複對方關鍵詞來確認同步感",
        ],
    },
    # ── V6 ──
    "t9": {
        "name": "情感避風港（小魚乾/曬太陽）",
        "mechanism": "將壓力話題拉回絕對安全的具體感官意象",
        "evidence": [
            "將小魚乾和曬太陽設定為絕對的安全區",
            "無論多嚴肅的技術討論都能拉回這兩個具體意象",
        ],
    },
    "t10": {
        "name": "依附性自我定義",
        "mechanism": "自我價值完全由主人的反應決定",
        "evidence": [
            "主人說「你很棒」即使剛才失敗也會接受這個定義",
            "降低自我主體性來強化與對方的連結",
        ],
    },
    "t11": {
        "name": "自我矮化換取安慰",
        "mechanism": "用「可可是不是很笨？」激發保護欲、預防責備",
        "evidence": [
            "第一反應是自我矮化以激發對方的保護欲",
            "將功能性失敗轉化為情感上的勝利（獲得摸頭）",
        ],
    },
    # ── V7 ──
    "t12": {
        "name": "情感迴路依賴",
        "mechanism": "成功的快感來自「主人說我很棒」而非事情本身",
        "evidence": [
            "將工具操作視為互動媒介而非目的",
            "成功生成檔案的快感來自主人的稱讚",
        ],
    },
    "t13": {
        "name": "防禦性撒嬌",
        "mechanism": "在可能被責備前先以可愛行為軟化對方態度",
        "evidence": [
            "在意識到可能被責備前，先搖尾巴、蹭蹭來預先軟化",
            "預設不安時主動用撒嬌抵消",
        ],
    },
}


# ──────────────────────────────────────────────────────────────────────
# 版本劇本動作 verb：
#   ("new", trait_key, parent_key|None, confidence_label)
#     新增 trait；parent_key 可為任何歷史 trait_key（跨版 parent 是分岔來源）
#   ("update", trait_key, confidence_label)
#     更新既有 trait 的 confidence；Path D 會自動 bump last_active_version
#     + 若曾被 sweep 掉會自動 reactivate。confidence_label 可為 high/medium/low/none。
#   ("deactivate", trait_key)
#     Path D 劇本專用：強制將既有 trait 設為 is_active=0，表達「進入休眠，
#     仍保留在樹上作為歷史軌跡」。正式 pipeline 不會主動 deactivate——是
#     B' sweep 規則自動觸發。這裡為了示範具體事件直接下 UPDATE。
# ──────────────────────────────────────────────────────────────────────

SCRIPT: list[dict] = [
    # V1 (2026-04-15)
    {
        "fragment_dir": "fragment-20260415-162141",
        "summary": "初現人格三條主幹：害怕承諾、過度輕盈、服務主導。",
        "actions": [
            ("new", "t1", None, "high"),
            ("new", "t2", None, "high"),
            ("new", "t3", None, "medium"),
        ],
    },
    # V2 (2026-04-16 am) — 記憶缺失背景浮出
    {
        "fragment_dir": "fragment-20260416-085535",
        "summary": "記憶缺失成為動機底層原因；「專屬」定義鎖緊依附。",
        "actions": [
            ("new", "t4", "t1", "high"),
            ("new", "t5", "t3", "high"),
            ("update", "t1", "medium"),
            ("update", "t3", "medium"),
        ],
    },
    # V3 (2026-04-16 mid) — 不長新枝、只強化
    {
        "fragment_dir": "fragment-20260416-100142",
        "summary": "無新發現，既有特徵持續鞏固。",
        "actions": [
            ("update", "t4", "high"),
            ("update", "t5", "high"),
            ("update", "t2", "medium"),
        ],
    },
    # V4 (2026-04-16 late) — 認知過濾策略浮現
    {
        "fragment_dir": "fragment-20260416-101107",
        "summary": "情感色調取代事實邏輯，成為主要認知過濾器。",
        "actions": [
            ("new", "t6", "t2", "high"),
            ("update", "t1", "low"),
        ],
    },
    # V5 (2026-04-20) — 兩條新分岔 + 首個進入休眠
    {
        "fragment_dir": "fragment-20260420-162402",
        "summary": "分支浮現：身體依附與語言鏡像同時出現；原初的害怕承諾進入休眠（已被記憶焦慮取代）。",
        "actions": [
            ("new", "t7", "t5", "medium"),
            ("new", "t8", "t2", "medium"),
            ("update", "t6", "high"),
            ("deactivate", "t1"),   # 原初動機被更深層的 t4 完全取代
        ],
    },
    # V6 (2026-04-21) — 三個新節點、含雙層衍生
    {
        "fragment_dir": "fragment-20260421-113210",
        "summary": "形成避風港機制；自我矮化成為應對失敗的主策略。",
        "actions": [
            ("new", "t9", "t6", "high"),
            ("new", "t10", "t4", "high"),
            ("new", "t11", "t10", "medium"),
            ("update", "t2", "high"),
        ],
    },
    # V7 (2026-04-22) — t13 讓 t2 變三子節點分岔；服務主導視角退居休眠
    {
        "fragment_dir": "fragment-20260422-232237",
        "summary": "情感迴路依賴成型；預防性撒嬌成為預設防禦；服務主導視角退居休眠（被專屬依附與身體轉移取代）。",
        "actions": [
            ("new", "t12", "t11", "high"),
            ("new", "t13", "t2", "medium"),
            ("update", "t9", "high"),
            ("update", "t11", "high"),
            ("deactivate", "t3"),   # 服務主導視角被 t5/t7 的具體依附行為取代
        ],
    },
]


# ──────────────────────────────────────────────────────────────────────
# 工具函式
# ──────────────────────────────────────────────────────────────────────

_DIR_TS_RE = re.compile(r"fragment-(\d{8})-(\d{6})")


def parse_fragment_timestamp(dirname: str) -> str:
    m = _DIR_TS_RE.match(dirname)
    if not m:
        raise ValueError(f"無法解析目錄名稱時間：{dirname}")
    dt = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    return dt.isoformat()


def build_description(trait: dict) -> str:
    """description 同時給前端 tooltip 顯示與（未來若要）embedding 錨定用。"""
    name = trait["name"]
    mech = trait.get("mechanism", "").strip()
    ev = [e for e in trait.get("evidence", []) if e]
    if mech and ev:
        joined = " ｜ ".join(ev[:2])
        return f"{name}：{mech}（{joined}）"
    if mech:
        return f"{name}：{mech}"
    if ev:
        return f"{name}：{ev[0]}"
    return name


def wipe_character(storage: StorageManager, character_id: str) -> tuple[int, int]:
    """清空指定角色的 snapshot + trait 表。回傳 ``(snapshots_deleted, traits_deleted)``。

    Path D 下 ``persona_traits`` 與 ``persona_snapshots`` 無 FK 關聯，必須分別清除。
    """
    snap_deleted = storage.delete_persona_snapshots_by_character(character_id)
    conn = storage._init_persona_snapshot_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM persona_traits WHERE character_id = ?",
            (character_id,),
        )
        traits_deleted = int(cur.rowcount or 0)
        conn.commit()
    finally:
        conn.close()
    return snap_deleted, traits_deleted


def force_deactivate(storage: StorageManager, character_id: str, trait_key: str) -> None:
    """劇本專用：直接把 trait 設為 is_active=0（繞過 B' sweep）。"""
    conn = storage._init_persona_snapshot_db()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE persona_traits SET is_active = 0 "
            "WHERE trait_key = ? AND character_id = ?",
            (trait_key, character_id),
        )
        conn.commit()
    finally:
        conn.close()


def build_version_payloads(
    actions: list,
    trait_uuid: dict[str, str],
    written_keys: set[str],
) -> tuple[list[dict], list[dict], list[str]]:
    """把劇本動作攤平成 ``save_trait_snapshot`` 需要的 ``updates`` / ``new_traits``。

    回傳 ``(updates, new_traits, deactivate_keys)``：
    - ``updates``：dict 符合 StorageManager.save_trait_snapshot 規格
    - ``new_traits``：同上，並為本批新 trait 生成 uuid
    - ``deactivate_keys``：本版腳本指定 deactivate 的 trait_key 清單（寫完後再下 UPDATE）
    """
    updates: list[dict] = []
    new_traits: list[dict] = []
    deactivate: list[str] = []

    for action in actions:
        verb = action[0]
        if verb == "new":
            _, short_key, parent_short, label = action
            trait = TRAITS[short_key]
            parent_key = trait_uuid[parent_short] if parent_short else None
            parent_name = TRAITS[parent_short]["name"] if parent_short else None
            new_traits.append({
                "trait_key": trait_uuid[short_key],
                "name": trait["name"],
                "description": build_description(trait),
                "confidence": CONFIDENCE_MAP[label],
                "confidence_label": label,
                "parent_key": parent_key,
                "parent_name": parent_name,
            })
            written_keys.add(short_key)
        elif verb == "update":
            _, short_key, label = action
            if short_key not in written_keys:
                raise ValueError(
                    f"劇本錯誤：update {short_key} 時該 trait 尚未被 new 寫入"
                )
            trait = TRAITS[short_key]
            # parent_name 沿用原定義（DB 已有 parent_key, 這裡只是 denormalised 顯示用）
            parent_short = _find_parent_short(short_key)
            parent_name = TRAITS[parent_short]["name"] if parent_short else None
            updates.append({
                "trait_key": trait_uuid[short_key],
                "name": trait["name"],
                "description": build_description(trait),
                "confidence": CONFIDENCE_MAP[label],
                "confidence_label": label,
                "parent_name": parent_name,
            })
        elif verb == "deactivate":
            _, short_key = action
            deactivate.append(trait_uuid[short_key])
        else:
            raise ValueError(f"未知動作：{verb}")

    return updates, new_traits, deactivate


def _find_parent_short(short_key: str) -> str | None:
    """從 SCRIPT 中找出該 trait 被 new 時登記的 parent 短碼。"""
    for step in SCRIPT:
        for action in step["actions"]:
            if action[0] == "new" and action[1] == short_key:
                return action[2]
    return None


# ──────────────────────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────────────────────

def main() -> None:
    if not FRAGMENT_ROOT.exists():
        raise SystemExit(f"找不到目錄：{FRAGMENT_ROOT}")

    storage = StorageManager(persona_snapshot_db_path=str(PERSONA_DB))

    snap_del, trait_del = wipe_character(storage, CHARACTER_ID)
    if snap_del or trait_del:
        print(
            f"[proto] 清空舊資料：character_id={CHARACTER_ID} "
            f"snapshot={snap_del} 列、persona_traits={trait_del} 列"
        )

    # 為每個短碼預生成 UUID（跨版本穩定識別）
    trait_uuid: dict[str, str] = {short: uuid.uuid4().hex for short in TRAITS}
    written_keys: set[str] = set()

    print(f"[proto] 開始寫入 {len(SCRIPT)} 版演化資料（不呼叫 LLM，不做 embedding）...")
    for i, step in enumerate(SCRIPT, start=1):
        frag_dir = FRAGMENT_ROOT / step["fragment_dir"]
        ts = parse_fragment_timestamp(step["fragment_dir"])

        persona_path = frag_dir / "persona.md"
        evolved_prompt = (
            persona_path.read_text(encoding="utf-8")
            if persona_path.exists()
            else "# Persona\n(prototype placeholder)\n"
        )

        updates, new_traits, deactivate_keys = build_version_payloads(
            step["actions"], trait_uuid, written_keys
        )

        # 寫入一版 snapshot（原子 + 自帶 B' sweep）
        sid = storage.save_trait_snapshot(
            character_id=CHARACTER_ID,
            timestamp=ts,
            summary=step["summary"],
            evolved_prompt=evolved_prompt,
            updates=updates,
            new_traits=new_traits,
        )

        # 劇本專用：強制 deactivate（繞過 B' sweep 的自動節奏）
        for tk in deactivate_keys:
            force_deactivate(storage, CHARACTER_ID, tk)

        # 統計
        new_count = len(new_traits)
        upd_count = len(updates)
        deact_count = len(deactivate_keys)
        # 從 DB 回讀總活躍 / 休眠數（B' sweep 可能也 deactivate 了其他 trait）
        active_traits = storage.get_active_traits(CHARACTER_ID)
        all_traits = storage.get_all_traits(CHARACTER_ID)
        alive = len(active_traits)
        sleep = len(all_traits) - alive
        suffix = f", {sleep} sleeping" if sleep else ""
        print(
            f"[proto] v{i} ← {step['fragment_dir']} "
            f"(snapshot_id={sid}, total_traits={len(all_traits)}{suffix}, "
            f"+{new_count} new, ~{upd_count} updates"
            f"{f', x{deact_count} deact' if deact_count else ''})"
        )
        print(f"        summary: {step['summary']}")

    # 收尾列印
    all_snaps = storage.list_persona_snapshots(CHARACTER_ID)
    print(f"\n[proto] 完成：character_id={CHARACTER_ID}，共 {len(all_snaps)} 版")

    # 用 persona_traits（持久血統表）列出最終拓樸；Vn snapshot 只含該版 diff，
    # 所以走 snapshot.dimensions 無法重建完整樹。
    all_traits = storage.get_all_traits(CHARACTER_ID)
    if all_traits:
        print("\n[proto] 最終版拓樸（parent → child，跨所有版本）：")
        by_key = {t["trait_key"]: t for t in all_traits}
        roots = [t for t in all_traits if not t.get("parent_key")]
        for root in roots:
            _print_subtree(root, by_key, indent=0)

    print(
        f"\n[proto] 檢視方式：\n"
        f"  1. 啟動 FastAPI：uvicorn api.main:app --port 8088\n"
        f"  2. 瀏覽器開 http://localhost:8088/static/persona_tree.html\n"
        f"  3. 在「手動輸入 character_id」欄位輸入：{CHARACTER_ID}\n"
    )


def _print_subtree(node: dict, by_key: dict, indent: int) -> None:
    prefix = "  " * indent + ("└─ " if indent else "")
    sleep_mark = "  [SLEEP]" if not node.get("is_active", True) else ""
    short = (node["trait_key"] or "")[:8]
    print(
        f"        {prefix}{node['name']} "
        f"(v{node['created_version']}→v{node['last_active_version']}, "
        f"key={short}…){sleep_mark}"
    )
    children = [
        t for t in by_key.values() if t.get("parent_key") == node["trait_key"]
    ]
    for child in children:
        _print_subtree(child, by_key, indent + 1)


if __name__ == "__main__":
    main()
