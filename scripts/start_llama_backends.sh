#!/usr/bin/env bash
# ==============================================================================
# Start llama-server instances for Index-Translate (8080) and Qwen3-ASR (8099)
# Strict Cross-Platform Parity with scripts/start_llama_backends.ps1
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

# 1. Resolve Python
PYTHON="python3"
if [ -f "$ROOT/venv/bin/python" ]; then
    PYTHON="$ROOT/venv/bin/python"
elif command -v python3 &> /dev/null; then
    PYTHON="python3"
fi

# 2. Resolve llama engine binary
LLAMA_EXE=""
if [ -n "$TRANSLATION_LLAMA_EXE" ] && [ -f "$TRANSLATION_LLAMA_EXE" ]; then
    LLAMA_EXE="$TRANSLATION_LLAMA_EXE"
elif command -v llama &> /dev/null; then
    LLAMA_EXE="$(command -v llama)"
elif command -v llama-server &> /dev/null; then
    LLAMA_EXE="$(command -v llama-server)"
elif [ -f "$HOME/.local/bin/llama" ]; then
    LLAMA_EXE="$HOME/.local/bin/llama"
elif [ -f "/usr/local/bin/llama" ]; then
    LLAMA_EXE="/usr/local/bin/llama"
elif [ -f "$ROOT/tools/llama-server/llama-server" ]; then
    LLAMA_EXE="$ROOT/tools/llama-server/llama-server"
elif [ -f "$ROOT/tools/llama" ]; then
    LLAMA_EXE="$ROOT/tools/llama"
fi

if [ -z "$LLAMA_EXE" ] || [ ! -x "$LLAMA_EXE" ]; then
    echo "[Error] llama engine not found or not executable!"
    echo "Please install llama via: curl -fsSL https://llama.app/install.sh | bash"
    echo "or put llama-server into tools/llama-server/ and chmod +x"
    exit 1
fi

EXE_NAME="$(basename "$LLAMA_EXE")"
IS_CLI=0
if [ "$EXE_NAME" = "llama" ] || [ "$EXE_NAME" = "llama-cli" ]; then
    IS_CLI=1
fi

# 3. Resolve Model Paths via Python SSOT
INDEX_GGUF="$("$PYTHON" -c "from server.config import get_model_path; print(get_model_path('Index-Translate-2B.Q4_K_M.gguf') or '')" 2>/dev/null || true)"
if [ -z "$INDEX_GGUF" ] || [ ! -f "$INDEX_GGUF" ]; then
    # Fallback to candidates
    for cand in "$ROOT/models/Index-Translate/Index-Translate-2B.Q4_K_M.gguf" \
                "/mnt/d/TranslationDesktop/models/Index-Translate/Index-Translate-2B.Q4_K_M.gguf" \
                "$ROOT/models/Index-Translate-2B.Q4_K_M.gguf"; do
        if [ -f "$cand" ]; then
            INDEX_GGUF="$cand"
            break
        fi
    done
fi

QWEN_PATHS="$("$PYTHON" -c "from server.config import QWEN3_ASR_MODEL, QWEN3_ASR_MMPROJ; print(f'{QWEN3_ASR_MODEL}|{QWEN3_ASR_MMPROJ}')" 2>/dev/null || true)"
QWEN_MODEL="${QWEN_PATHS%%|*}"
QWEN_MMPROJ="${QWEN_PATHS##*|}"

# Common memory cap & slot eviction controls (SSOT)
COMMON_MEM_ARGS=(
    "--cache-ram" "512"
    "--ctx-checkpoints" "1"
    "--no-cache-idle-slots"
)

# Helper function to test TCP port
test_port() {
    local port="$1"
    if command -v nc &> /dev/null; then
        nc -z 127.0.0.1 "$port" 2>/dev/null
    elif command -v lsof &> /dev/null; then
        lsof -i :"$port" -sTCP:LISTEN &>/dev/null
    else
        bash -c "cat < /dev/null > /dev/tcp/127.0.0.1/$port" 2>/dev/null
    fi
}

# 4. Start 8080 Index-Translate
if ! test_port 8080; then
    echo "Starting 8080 Index-Translate ($EXE_NAME)..."
    CMD_80=("$LLAMA_EXE")
    if [ "$IS_CLI" -eq 1 ]; then
        CMD_80+=("serve")
    fi
    CMD_80+=(
        "-m" "$INDEX_GGUF"
        "--host" "127.0.0.1"
        "--port" "8080"
        "-ngl" "60"
        "-c" "2048"
        "-t" "4"
        "-np" "3"
        "--no-webui"
        "-lv" "4"
        "--reasoning" "off"
        "${COMMON_MEM_ARGS[@]}"
    )
    "${CMD_80[@]}" > "$LOG_DIR/llama.out" 2> "$LOG_DIR/llama.err" &
else
    echo "8080 already up."
fi

# 5. Start 8099 Qwen3-ASR
if ! test_port 8099; then
    echo "Starting 8099 Qwen3-ASR ($EXE_NAME)..."
    CMD_99=("$LLAMA_EXE")
    if [ "$IS_CLI" -eq 1 ]; then
        CMD_99+=("serve")
    fi
    CMD_99+=(
        "-m" "$QWEN_MODEL"
        "--mmproj" "$QWEN_MMPROJ"
        "--host" "127.0.0.1"
        "--port" "8099"
        "-ngl" "99"
        "-c" "2048"
        "-t" "4"
        "-np" "4"
        "--no-webui"
        "${COMMON_MEM_ARGS[@]}"
    )
    "${CMD_99[@]}" > "$LOG_DIR/llama8099.out" 2> "$LOG_DIR/llama8099.err" &
else
    echo "8099 already up."
fi

echo "Waiting for backends to report health..."
for i in {1..30}; do
    READY_80=0
    READY_99=0
    if curl -s http://127.0.0.1:8080/v1/models | grep -q "Index"; then
        READY_80=1
    fi
    if curl -s http://127.0.0.1:8099/v1/models | grep -q "Qwen"; then
        READY_99=1
    fi
    if [ "$READY_80" -eq 1 ] && [ "$READY_99" -eq 1 ]; then
        echo "[OK] All backends (8080 MT, 8099 ASR) are healthy and ready!"
        exit 0
    fi
    sleep 1
done

echo "[Warning] Timeout waiting for backends. Check logs in $LOG_DIR."
