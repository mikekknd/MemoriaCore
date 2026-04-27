#!/usr/bin/env python3
"""
scripts/backfill_user_id_from_telegram.py

從 conversation_sessions 讀取 Telegram channel_uid，
將所有資料表中 user_id='default' 的紀錄反推為對應的 Telegram user_id。

使用場景：單一 SU（Super User）+ Telegram 頻道的部署環境。

⚠️  執行前必須備份 DB。此操作不可逆。
    cp memory.db memory.db.bak

用法：
    python scripts/backfill_user_id_from_telegram.py \\
        --db path/to/memory.db \\
        [--user-id TELEGRAM_UID] \\
        [--dry-run]

選項：
    --db        SQLite DB 路徑（必填）
    --user-id   直接指定 Telegram user_id（省略則從 conversation_sessions 自動偵測）
    --dry-run   僅統計受影響筆數，不執行任何修改

注意事項：
    - 此腳本只更新 user_id='default' 的紀錄，已有其他 user_id 的不動。
    - conversation_sessions 只更新 channel='telegram' 的 session。
    - persona 相關表（persona_snapshots / persona_traits）不在此腳本範圍，
      那些資料以 character_id 為主鍵，不需要 user_id 反推。
"""
import argparse
import sqlite3
import sys
from pathlib import Path


def detect_telegram_uid(conn: sqlite3.Connection) -> str | None:
    """從 conversation_sessions 自動偵測最常見的 Telegram channel_uid。"""
    cur = conn.execute(
        "SELECT channel_uid, COUNT(*) AS cnt "
        "FROM conversation_sessions "
        "WHERE channel = 'telegram' "
        "  AND channel_uid IS NOT NULL AND channel_uid != '' "
        "GROUP BY channel_uid "
        "ORDER BY cnt DESC "
        "LIMIT 1"
    )
    row = cur.fetchone()
    return row[0] if row else None


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    )
    return cur.fetchone() is not None


def count_affected(conn: sqlite3.Connection) -> dict[str, int]:
    """統計各表中 user_id='default' 的筆數（dry-run 顯示用）。"""
    queries = {
        "memory_blocks_v3": (
            "SELECT COUNT(*) FROM memory_blocks_v3 WHERE user_id = 'default'"
        ),
        "core_memories": (
            "SELECT COUNT(*) FROM core_memories WHERE user_id = 'default'"
        ),
        "user_profile": (
            "SELECT COUNT(*) FROM user_profile WHERE user_id = 'default'"
        ),
        "user_profile_vectors": (
            "SELECT COUNT(*) FROM user_profile_vectors WHERE user_id = 'default'"
        ),
        "topic_cache": (
            "SELECT COUNT(*) FROM topic_cache WHERE user_id = 'default'"
        ),
        "conversation_sessions (telegram only)": (
            "SELECT COUNT(*) FROM conversation_sessions "
            "WHERE channel = 'telegram' AND user_id = 'default'"
        ),
    }
    results: dict[str, int] = {}
    for label, sql in queries.items():
        table = label.split()[0]  # 取第一個字（table name）
        if not _table_exists(conn, table):
            results[label] = 0
            continue
        try:
            results[label] = conn.execute(sql).fetchone()[0]
        except sqlite3.OperationalError:
            results[label] = 0
    return results


def run_backfill(conn: sqlite3.Connection, target_uid: str) -> dict[str, int]:
    """
    執行反推。回傳各表實際更新筆數。

    策略：
    - memory_blocks_v3 / core_memories / topic_cache：user_id 不是 PK，直接 UPDATE。
    - user_profile / user_profile_vectors：user_id 是 PK 的一部分，需要 INSERT + DELETE。
      順序：先 INSERT vectors（FK 依賴 profile）→ INSERT profile → DELETE vectors → DELETE profile。
      實際上因為 FK 已 OFF，順序可放寬，但仍保持直覺順序。
    - conversation_sessions：只更新 channel='telegram' 的 session。
    """
    updated: dict[str, int] = {}

    # ── 1. 簡單 UPDATE 類 ──────────────────────────────────────────
    for table in ("memory_blocks_v3", "core_memories", "topic_cache"):
        if not _table_exists(conn, table):
            updated[table] = 0
            continue
        cur = conn.execute(
            f"UPDATE {table} SET user_id = ? WHERE user_id = 'default'",
            (target_uid,),
        )
        updated[table] = cur.rowcount

    # ── 2. user_profile（PK 含 user_id，需 INSERT+DELETE）────────────
    if _table_exists(conn, "user_profile"):
        # 先插入新 user_id 的 profile（INSERT OR IGNORE 防 PK 衝突）
        conn.execute(
            "INSERT OR IGNORE INTO user_profile "
            "  (user_id, fact_key, fact_value, category, confidence, "
            "   timestamp, source_context, visibility) "
            "SELECT ?, fact_key, fact_value, category, confidence, "
            "       timestamp, source_context, visibility "
            "FROM user_profile WHERE user_id = 'default'",
            (target_uid,),
        )
        # 刪除舊 default 的 profile（CASCADE 會帶走 vectors，但 FK 已 OFF，需手動）
        cur = conn.execute(
            "DELETE FROM user_profile WHERE user_id = 'default'"
        )
        updated["user_profile"] = cur.rowcount
    else:
        updated["user_profile"] = 0

    # ── 3. user_profile_vectors（PK 含 user_id，同策略）─────────────
    if _table_exists(conn, "user_profile_vectors"):
        conn.execute(
            "INSERT OR IGNORE INTO user_profile_vectors "
            "  (user_id, fact_key, fact_value, fact_vector) "
            "SELECT ?, fact_key, fact_value, fact_vector "
            "FROM user_profile_vectors WHERE user_id = 'default'",
            (target_uid,),
        )
        cur = conn.execute(
            "DELETE FROM user_profile_vectors WHERE user_id = 'default'"
        )
        updated["user_profile_vectors"] = cur.rowcount
    else:
        updated["user_profile_vectors"] = 0

    # ── 4. conversation_sessions（只更新 Telegram channel）───────────
    if _table_exists(conn, "conversation_sessions"):
        cur = conn.execute(
            "UPDATE conversation_sessions SET user_id = ? "
            "WHERE channel = 'telegram' AND user_id = 'default'",
            (target_uid,),
        )
        updated["conversation_sessions"] = cur.rowcount
    else:
        updated["conversation_sessions"] = 0

    return updated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill user_id='default' → Telegram channel_uid"
    )
    parser.add_argument("--db", required=True, help="SQLite DB 路徑（必填）")
    parser.add_argument(
        "--user-id",
        help="直接指定 Telegram user_id（省略則從 conversation_sessions 自動偵測）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="僅統計筆數，不執行任何修改",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[錯誤] 找不到 DB：{db_path}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    # 暫時關閉 FK，讓 INSERT/DELETE 順序更彈性（腳本結束後自動恢復）
    conn.execute("PRAGMA foreign_keys = OFF")

    # ── 決定目標 user_id ──
    target_uid = args.user_id
    if not target_uid:
        target_uid = detect_telegram_uid(conn)
        if not target_uid:
            print(
                "[錯誤] 找不到任何 Telegram channel_uid。\n"
                "       請確認 conversation_sessions 中有 channel='telegram' 的記錄，\n"
                "       或使用 --user-id 手動指定。",
                file=sys.stderr,
            )
            conn.close()
            sys.exit(1)
        print(f"[偵測] 自動使用 Telegram uid：{target_uid}")
    else:
        print(f"[指定] 目標 user_id：{target_uid}")

    # ── dry-run：只顯示筆數 ──
    if args.dry_run:
        affected = count_affected(conn)
        print(f"\n[DRY RUN] 若執行，將把 user_id='default' → '{target_uid}'：")
        for label, count in affected.items():
            print(f"  {label:<40} {count:>6} 筆")
        print("\n（DRY RUN 模式，未執行任何修改）")
        conn.close()
        return

    # ── 確認提示 ──
    print(f"\n⚠️  即將把所有 user_id='default' 改為 '{target_uid}'。")
    print("   請確認已備份 DB（cp memory.db memory.db.bak）。")
    answer = input("   確認執行？[y/N] ").strip().lower()
    if answer != "y":
        print("已取消。")
        conn.close()
        return

    # ── 執行反推 ──
    with conn:  # BEGIN/COMMIT 包圍
        updated = run_backfill(conn, target_uid)

    print(f"\n✅ 反推完成（user_id='default' → '{target_uid}'）：")
    for table, count in updated.items():
        print(f"  {table:<40} {count:>6} 筆更新")

    conn.close()


if __name__ == "__main__":
    main()
