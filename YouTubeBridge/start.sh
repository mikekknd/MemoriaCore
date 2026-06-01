#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$SCRIPT_DIR"

API_PORT=8091
LOG_DIR="$PROJECT_ROOT/runtime/log"
OUT_LOG="$LOG_DIR/youtube_bridge_8091_hot_reload.out.log"
ERR_LOG="$LOG_DIR/youtube_bridge_8091_hot_reload.err.log"
HOT_RELOAD_SCRIPT="run_server_hot_reload.py"
mkdir -p "$LOG_DIR"

PARENT_VENV="$PROJECT_ROOT/venv_ai_memory/bin/python"
LOCAL_VENV="$SCRIPT_DIR/venv/bin/python"

if [[ -x "$PARENT_VENV" ]]; then
    echo "[INFO] Using parent venv: venv_ai_memory"
    PYTHON="$PARENT_VENV"
elif [[ -x "$LOCAL_VENV" ]]; then
    echo "[INFO] Using local venv"
    PYTHON="$LOCAL_VENV"
elif command -v python3 >/dev/null 2>&1; then
    echo "[WARN] No venv found, falling back to system Python."
    PYTHON="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
    echo "[WARN] No venv found, falling back to system Python."
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

echo "============================================"
echo "  YouTubeBridge - API Launcher"
echo "============================================"
echo

echo "[INFO] Cleaning any current listener/process tree on port $API_PORT before start..."
if ! cleanup_port; then
    exit 1
fi

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

if ! "$PYTHON" -c "import fastapi, pydantic, requests, uvicorn" >/dev/null 2>&1; then
    echo "[INFO] Installing dependencies from requirements.txt ..."
    if ! "$PYTHON" -m pip install -r requirements.txt; then
        echo "[ERROR] Failed to install dependencies."
        exit 1
    fi
fi

if [[ -z "${MEMORIACORE_ADMIN_BYPASS:-}" ]]; then
    export MEMORIACORE_ADMIN_BYPASS=1
fi

echo
echo "============================================"
echo "  Starting YouTubeBridge hot reload server"
echo
echo "  Studio UI    : http://localhost:$API_PORT/studio/"
echo "  Legacy UI    : http://localhost:$API_PORT/ui/"
echo "  API server   : http://localhost:$API_PORT"
echo "  API docs     : http://localhost:$API_PORT/docs"
echo "============================================"
echo
echo "[INFO] Keep this shell open while using the API. Close it or press Ctrl+C to stop."
echo "[INFO] stdout is mirrored to: $OUT_LOG"
echo "[INFO] stderr is mirrored to: $ERR_LOG"
echo "[INFO] Open the Studio UI after the server reports that it is running."

"$PYTHON" "$HOT_RELOAD_SCRIPT" > >(tee -a "$OUT_LOG") 2> >(tee -a "$ERR_LOG" >&2)
EXIT_CODE=$?

echo
echo "[INFO] YouTubeBridge API hot reload server exited with code $EXIT_CODE."
echo "[INFO] Cleaning any remaining listener on port $API_PORT..."
cleanup_port || true
exit "$EXIT_CODE"
