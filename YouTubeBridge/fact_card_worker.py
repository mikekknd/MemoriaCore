"""FactCards 自動補卡 worker。

這支腳本由 8091 背景排程啟動成獨立 process，避免 Gemini CLI 卡住時拖住
YouTubeBridge server process 的 request / SSE / health path。
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BRIDGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = BRIDGE_ROOT.parent
for candidate in (str(BRIDGE_ROOT), str(PROJECT_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from bridge_engine import YouTubeBridgeManager
from storage import BridgeStorage


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate and import one YouTubeBridge FactCard file.")
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--pack-id", required=True, type=int)
    parser.add_argument("--output-name", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        storage = BridgeStorage(Path(args.db_path))
        manager = YouTubeBridgeManager(storage)
        result = manager.generate_fact_cards_with_gemini(
            args.session_id,
            topic=args.topic,
            pack_id=int(args.pack_id),
            output_name=args.output_name,
            timeout_seconds=int(args.timeout_seconds),
        )
        print(json.dumps(result, ensure_ascii=True, default=str))
        return 0
    except Exception as exc:
        payload = {
            "status": "failed",
            "error": str(exc)[:800],
            "trace_tail": traceback.format_exc()[-1200:],
        }
        print(json.dumps(payload, ensure_ascii=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
