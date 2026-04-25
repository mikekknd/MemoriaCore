"""Path D Snapshot 批次生成腳本。

從 `PersonaProbe/result/fragment-*/` 的既有資料出發，以真實 LLM 萃取
產生具有正確 UUID（uuid4().hex）的 Path D snapshot，寫入 `persona_snapshots.db`。

使用方式：
    python scripts/generate_path_d_snapshots.py --model llama3.2 --reset

    python scripts/generate_path_d_snapshots.py --model llama3.2 --character-id my-char --ollama-base-url http://localhost:11434
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

# ── 路徑初始化 ──────────────────────────────────────────────────────────────

_ROOT = Path(__file__).parent.parent
_PROBE_DIR = _ROOT / "PersonaProbe"
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_PROBE_DIR))

# ── 引用核心模組 ────────────────────────────────────────────────────────────

from core.storage_manager import StorageManager
from core.persona_evolution.snapshot_store import PersonaSnapshotStore
from core.persona_evolution.extractor import (
    TRAIT_V1_SCHEMA,
    TRAIT_VN_SCHEMA,
    parse_trait_v1,
    parse_trait_vn,
)
from core.persona_evolution.trait_diff import TraitDiff

from llm_client import LLMClient, LLMConfig
from probe_engine import (
    build_trait_v1_prompt,
    build_trait_vn_prompt,
    build_trait_report_prompt,
)

# ── CLI 引數 ────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="使用 fragment 目錄資料執行 Path D snapshot 批次寫入（需 Ollama 正常運作）"
    )
    p.add_argument(
        "--model",
        required=True,
        help="Ollama 模型名稱（如 llama3.2）",
    )
    p.add_argument(
        "--character-id",
        default="catgirl-fragment",
        help="寫入 persona_snapshots.db 的角色 ID（預設 catgirl-fragment）",
    )
    p.add_argument(
        "--ollama-base-url",
        default="http://localhost:11434",
        help="Ollama base URL（預設 http://localhost:11434）",
    )
    p.add_argument(
        "--reset",
        action="store_true",
        help="寫入前先清除該 character_id 的所有舊 snapshot + trait",
    )
    return p

# ── LLM 重試包裝 ────────────────────────────────────────────────────────────

def _call_llm(
    client: LLMClient,
    messages: list[dict],
    response_format: dict | None = None,
    max_retries: int = 2,
) -> str:
    for attempt in range(max_retries + 1):
        try:
            return client.chat(messages, response_format=response_format)
        except Exception as e:
            if attempt < max_retries:
                wait = 5 * (2 ** attempt)
                print(f"  [retry] attempt {attempt + 1} failed, waiting {wait}s: {e}")
                time.sleep(wait)
            else:
                raise RuntimeError(
                    f"LLM call failed after {max_retries} retries: {e}"
                ) from e


# ── 主要流程 ────────────────────────────────────────────────────────────────

def main() -> None:
    args = _build_parser().parse_args()

    # ── Storage + Store 初始化 ────────────────────────────────────────
    storage = StorageManager(
        persona_snapshot_db_path=str(_ROOT / "persona_snapshots.db"),
    )
    store = PersonaSnapshotStore(storage)

    # ── LLM client 初始化 ───────────────────────────────────────────
    llm = LLMClient(
        LLMConfig(
            provider="ollama",
            model=args.model,
            ollama_base_url=args.ollama_base_url,
            temperature=0.7,
        )
    )

    # ── 探索 fragment 目錄（按時間排序） ─────────────────────────────
    FRAGMENT_ROOT = _PROBE_DIR / "result"
    DIR_RE = re.compile(r"fragment-(\d{8})-(\d{6})")

    fragment_dirs = sorted(
        [
            d
            for d in FRAGMENT_ROOT.iterdir()
            if d.is_dir() and DIR_RE.match(d.name)
        ],
        key=lambda d: d.name,  # lexicographic = chronological for this pattern
    )

    if not fragment_dirs:
        print(f"[error] No fragment directories found in {FRAGMENT_ROOT}")
        sys.exit(1)

    print(f"[init] Found {len(fragment_dirs)} fragment directories")
    print(f"[init] character_id={args.character_id}, model={args.model}")

    # ── Optional reset ────────────────────────────────────────────────
    if args.reset:
        snap_del = storage.delete_persona_snapshots_by_character(args.character_id)
        conn = storage._init_persona_snapshot_db()
        try:
            cur = conn.cursor()
            cur.execute(
                "DELETE FROM persona_traits WHERE character_id = ?",
                (args.character_id,),
            )
            trait_del = int(cur.rowcount or 0)
            conn.commit()
        finally:
            conn.close()
        print(f"[reset] Cleared {snap_del} snapshots and {trait_del} traits for {args.character_id}")

    # ── 迭代處理每個 fragment ─────────────────────────────────────────
    for frag_idx, fragment_dir in enumerate(fragment_dirs, start=1):
        frag_name = fragment_dir.name
        print(f"\n[>>>] Processing {frag_name} ({frag_idx}/{len(fragment_dirs)})")

        # ── 讀取輸入檔案 ─────────────────────────────────────────
        frag_input_path = fragment_dir / "fragment-input.md"
        frag_persona_path = fragment_dir / "persona.md"
        frag_report_path = fragment_dir / "probe-report.md"

        missing = [
            f.name
            for f in [frag_input_path, frag_persona_path, frag_report_path]
            if not f.exists()
        ]
        if missing:
            print(f"[skip] Missing files in {frag_name}: {missing}")
            continue

        fragments_text = frag_input_path.read_text(encoding="utf-8")
        frag_persona = frag_persona_path.read_text(encoding="utf-8")

        # 從 probe-report.md 取第一行非 # 開頭文字作為 summary
        probe_report_lines = frag_report_path.read_text(encoding="utf-8").splitlines()
        summary = ""
        for line in probe_report_lines:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            summary = s
            break

        # ── V1 vs Vn 判斷 ─────────────────────────────────────────
        active_traits = store.list_active_traits(args.character_id)
        is_v1 = len(active_traits) == 0

        print(
            f"  [state] is_v1={is_v1}, active_traits={len(active_traits)}, "
            f"existing_persona length={len(frag_persona)}"
        )

        # ── LLM Call 1: Trait 萃取 ────────────────────────────────
        print(f"  [llm-1] Trait extraction (V1={'v1' if is_v1 else 'vn'})...")
        if is_v1:
            messages = build_trait_v1_prompt(fragments_text, existing_persona="")
            raw = _call_llm(llm, messages, response_format=TRAIT_V1_SCHEMA)
            new_traits = parse_trait_v1(raw)
            trait_diff = TraitDiff(updates=[], new_traits=new_traits)
        else:
            messages = build_trait_vn_prompt(fragments_text, frag_persona, active_traits)
            raw = _call_llm(llm, messages, response_format=TRAIT_VN_SCHEMA)
            trait_diff = parse_trait_vn(raw)

        if not trait_diff.updates and not trait_diff.new_traits:
            print(f"  [warn] No parseable traits from {frag_name} — skipping snapshot write")
            print(f"  [raw-response] {repr(raw[:400])}")
            continue

        new_count = len(trait_diff.new_traits)
        upd_count = len(trait_diff.updates)
        print(f"  [llm-1] raw parsed → +{new_count} new, ~{upd_count} updates")

        # ── LLM Call 2: 敘事報告 ──────────────────────────────────
        # 重新取一次 active_traits（save_snapshot 之前的狀態）
        active_now = store.list_active_traits(args.character_id)
        print(f"  [llm-2] Report generation (active_traits={len(active_now)})...")
        try:
            report_messages = build_trait_report_prompt(
                trait_diff=trait_diff.model_dump(),
                active_traits=active_now,
                fragments_text=fragments_text,
            )
            report_text = _call_llm(llm, report_messages)
        except Exception as e:
            print(f"  [warn] Report generation failed: {e}")
            report_text = ""

        # ── 寫入 snapshot ─────────────────────────────────────────
        timestamp = frag_name  # "fragment-YYYYMMDD-HHMMSS" 保存時間順序
        try:
            sid = store.save_snapshot(
                character_id=args.character_id,
                trait_diff=trait_diff,
                summary=summary or f"Auto-generated from {frag_name}",
                evolved_prompt=frag_persona,
                timestamp=timestamp,
            )
        except Exception as e:
            print(f"[error] DB write failed for {frag_name}: {e}")
            print("Aborting. Use --reset to start fresh.")
            sys.exit(1)

        # ── 統計並列印進度 ────────────────────────────────────────
        all_traits = storage.get_all_traits(args.character_id)
        active_count = len(storage.get_active_traits(args.character_id))
        total_snaps = len(storage.list_persona_snapshots(args.character_id))

        print(
            f"  [done] snapshot_id={sid} | v{total_snaps} | "
            f"total_traits={len(all_traits)}, active={active_count} | "
            f"+{new_count} new, ~{upd_count} updates"
        )

    # ── 最终 Summary ─────────────────────────────────────────────────
    all_snaps = storage.list_persona_snapshots(args.character_id)
    all_traits = storage.get_all_traits(args.character_id)
    active_cnt = len(storage.get_active_traits(args.character_id))

    print("\n" + "=" * 60)
    print("Path D Snapshot Generation Summary")
    print("=" * 60)
    print(f"  character_id  : {args.character_id}")
    print(f"  fragments     : {len(fragment_dirs)}")
    print(f"  snapshots     : {len(all_snaps)}")
    print(f"  total traits  : {len(all_traits)}")
    print(f"  active        : {active_cnt}")
    print(f"  sleeping      : {len(all_traits) - active_cnt}")
    print()
    print("Topology (created_version order):")
    for t in sorted(all_traits, key=lambda x: x["created_version"]):
        pk = f"  ← {t['parent_key'][:8]}…" if t["parent_key"] else ""
        status = "  [SLEEP]" if not t["is_active"] else ""
        print(f"  v{t['created_version']}  {t['name']}{pk}{status}")
    print()
    print("View in browser:")
    print(f"  1. Start: uvicorn api.main:app --port 8088")
    print(f"  2. Open:  http://localhost:8088/static/persona_tree.html")
    print(f"  3. Input character_id: {args.character_id}")


if __name__ == "__main__":
    main()