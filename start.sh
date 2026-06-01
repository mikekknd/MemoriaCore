#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

API_PORT=8088
LOG_DIR="$SCRIPT_DIR/runtime/log"
OUT_LOG="$LOG_DIR/api_8088_hot_reload.out.log"
ERR_LOG="$LOG_DIR/api_8088_hot_reload.err.log"
HOT_RELOAD_SCRIPT="run_server_hot_reload.py"
mkdir -p "$LOG_DIR"

if [[ -x "$SCRIPT_DIR/venv_ai_memory/bin/python" ]]; then
    PYTHON="$SCRIPT_DIR/venv_ai_memory/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
    PYTHON="$(command -v python)"
else
    echo "[ERROR] Python is not installed or not in PATH."
    exit 1
fi

collect_port_pids() {
    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -tiTCP:"$API_PORT" -sTCP:LISTEN 2>/dev/null || true
        return
    fi
    if command -v fuser >/dev/null 2>&1; then
        fuser "$API_PORT/tcp" 2>/dev/null | tr ' ' '\n' | sed '/^$/d' || true
        return
    fi
    if command -v ss >/dev/null 2>&1; then
        ss -ltnp 2>/dev/null | awk -v port=":${API_PORT}" '
            $4 ~ port"$" {
                while (match($0, /pid=[0-9]+/)) {
                    print substr($0, RSTART + 4, RLENGTH - 4)
                    $0 = substr($0, RSTART + RLENGTH)
                }
            }
        ' || true
    fi
}

collect_reload_pids() {
    ps -eo pid=,args= 2>/dev/null | awk -v self="$$" -v script="$HOT_RELOAD_SCRIPT" '
        $1 != self && index($0, script) { print $1 }
    ' || true
}

kill_process_tree() {
    local pid="$1"
    [[ -z "$pid" || "$pid" == "$$" ]] && return

    if command -v pgrep >/dev/null 2>&1; then
        local child
        while IFS= read -r child; do
            [[ -n "$child" ]] && kill_process_tree "$child"
        done < <(pgrep -P "$pid" 2>/dev/null || true)
    fi

    if kill -0 "$pid" 2>/dev/null; then
        local cmdline
        cmdline="$(ps -p "$pid" -o args= 2>/dev/null || true)"
        echo "[KILL] PID=$pid $cmdline"
        kill "$pid" 2>/dev/null || true
    fi
}

cleanup_port() {
    local pids
    pids="$({ collect_port_pids; collect_reload_pids; } | sort -u)"

    if [[ -z "$pids" ]]; then
        echo "[OK] No LISTENING socket or hot reload supervisor found for port $API_PORT."
        return 0
    fi

    echo "[INFO] Cleaning listener/hot reload process tree on port $API_PORT..."
    local pid
    while IFS= read -r pid; do
        [[ -n "$pid" ]] && kill_process_tree "$pid"
    done <<< "$pids"

    sleep 0.8
    pids="$({ collect_port_pids; collect_reload_pids; } | sort -u)"
    if [[ -n "$pids" ]]; then
        while IFS= read -r pid; do
            if [[ -n "$pid" && "$pid" != "$$" ]]; then
                echo "[KILL] Forcing remaining listener PID=$pid"
                kill -KILL "$pid" 2>/dev/null || true
            fi
        done <<< "$pids"
    fi

    sleep 0.8
    pids="$({ collect_port_pids; collect_reload_pids; } | sort -u)"
    if [[ -n "$pids" ]]; then
        echo "[ERROR] Port $API_PORT or its hot reload supervisor is still active after cleanup: $pids"
        return 1
    fi

    echo "[OK] No LISTENING socket or hot reload supervisor remains for port $API_PORT."
    return 0
}

echo "[INFO] Cleaning any current listener/process tree on port $API_PORT before start..."
if ! cleanup_port; then
    exit 1
fi

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

echo "[OK] Port $API_PORT is free. Starting MemoriaCore API hot reload server in this foreground shell..."
echo "[INFO] Keep this shell open while using the API. Close it or press Ctrl+C to stop."
echo "[INFO] URL: http://localhost:$API_PORT"
echo "[INFO] stdout is mirrored to: $OUT_LOG"
echo "[INFO] stderr is mirrored to: $ERR_LOG"

"$PYTHON" "$HOT_RELOAD_SCRIPT" > >(tee -a "$OUT_LOG") 2> >(tee -a "$ERR_LOG" >&2)
EXIT_CODE=$?

echo
echo "[INFO] MemoriaCore API hot reload server exited with code $EXIT_CODE."
echo "[INFO] Cleaning any remaining listener on port $API_PORT..."
cleanup_port || true
exit "$EXIT_CODE"
