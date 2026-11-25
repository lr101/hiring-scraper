#!/usr/bin/env bash
# Run the job analysis models on the host for development (no Docker needed).
#   scripts/dev/local-models.sh start|stop|status
# Downloads the pinned llama.cpp release and GGUF weights once into
# ${RADIUS_LLM_HOME:-~/.cache/radius-llm}, then serves:
#   LLM       http://127.0.0.1:8091  (JOB_ANALYSIS_LLM_URL)
#   reranker  http://127.0.0.1:8092  (JOB_ANALYSIS_RERANK_URL)
# Versions match compose.yaml; see docs/local-models.md.
set -euo pipefail

LLAMA_BUILD=b12098
LLM_URL=https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/e87f176479d0855a907a41277aca2f8ee7a09523/Qwen3.5-4B-Q4_K_M.gguf
LLM_FILE=Qwen3.5-4B-Q4_K_M.gguf
LLM_ALIAS='unsloth/Qwen3.5-4B-GGUF@e87f176479d0:Qwen3.5-4B-Q4_K_M.gguf'
RERANK_URL=https://huggingface.co/ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF/resolve/a02f48bb4f057028298c21fa033da2b30d7742d5/qwen3-reranker-0.6b-q8_0.gguf
RERANK_FILE=qwen3-reranker-0.6b-q8_0.gguf
RERANK_ALIAS='ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF@a02f48bb4f05:qwen3-reranker-0.6b-q8_0.gguf'
HOME_DIR=${RADIUS_LLM_HOME:-$HOME/.cache/radius-llm}
THREADS=${LLM_THREADS:-4}
BIN="$HOME_DIR/bin/llama-$LLAMA_BUILD/llama-server"

install() {
  mkdir -p "$HOME_DIR/bin" "$HOME_DIR/models"
  if [[ ! -x "$BIN" ]]; then
    # SHA-256 digests published with the $LLAMA_BUILD release; the binary runs on the host.
    case "$(uname -s)-$(uname -m)" in
      Linux-x86_64) asset=ubuntu-x64; digest=7e6f56a0940e5db3744a766ba5480ab5e0ba30b61b656b682f3919666da6aae7 ;;
      Linux-aarch64) asset=ubuntu-arm64; digest=22a4bcd39fd8e77158030b16380f13e7d26d2b16ef875e093f385dc9ab7bfa13 ;;
      Darwin-arm64) asset=macos-arm64; digest=73371d09105a776f8025c9b4a9634865adfd31653aa5819a5f07c3f7822221b2 ;;
      Darwin-x86_64) asset=macos-x64; digest=611d0666affdee08b7b93ba0943a67f01b0ef948f7ddeb9b1dd0a67383add437 ;;
      *) echo "Unsupported platform; install llama.cpp $LLAMA_BUILD manually" >&2; exit 1 ;;
    esac
    curl -fsSL -o "$HOME_DIR/llama.tgz" \
      "https://github.com/ggml-org/llama.cpp/releases/download/$LLAMA_BUILD/llama-$LLAMA_BUILD-bin-$asset.tar.gz"
    if command -v sha256sum >/dev/null; then actual=$(sha256sum "$HOME_DIR/llama.tgz" | cut -d' ' -f1)
    else actual=$(shasum -a 256 "$HOME_DIR/llama.tgz" | cut -d' ' -f1); fi
    if [[ "$actual" != "$digest" ]]; then
      rm -f "$HOME_DIR/llama.tgz"
      echo "llama.cpp download does not match the pinned SHA-256; refusing to run it" >&2; exit 1
    fi
    tar xzf "$HOME_DIR/llama.tgz" -C "$HOME_DIR/bin" && rm "$HOME_DIR/llama.tgz"
  fi
}

serve() {  # name port model-file model-url alias extra-args...
  local name=$1 port=$2 file=$3 url=$4 alias=$5; shift 5
  if curl -fsS "http://127.0.0.1:$port/health" >/dev/null 2>&1; then
    echo "$name already running on :$port"; return
  fi
  nohup "$BIN" --host 127.0.0.1 --port "$port" --model "$HOME_DIR/models/$file" --model-url "$url" \
    --alias "$alias" --threads "$THREADS" --ctx-size 8192 --no-webui "$@" \
    >"$HOME_DIR/$name.log" 2>&1 &
  echo $! >"$HOME_DIR/$name.pid"
  echo "$name starting on :$port (log: $HOME_DIR/$name.log)"
}

case "${1:-start}" in
  start)
    install
    serve llm 8091 "$LLM_FILE" "$LLM_URL" "$LLM_ALIAS" --parallel 1 \
      --chat-template-kwargs '{"enable_thinking":false}'
    serve reranker 8092 "$RERANK_FILE" "$RERANK_URL" "$RERANK_ALIAS" --reranking \
      --batch-size 2048 --ubatch-size 2048
    ;;
  stop)
    for name in llm reranker; do
      [[ -f "$HOME_DIR/$name.pid" ]] && kill "$(cat "$HOME_DIR/$name.pid")" 2>/dev/null || true
      rm -f "$HOME_DIR/$name.pid"
    done
    ;;
  status)
    for port in 8091 8092; do
      printf '%s ' ":$port"; curl -fsS "http://127.0.0.1:$port/health" || printf 'down'; echo
    done
    ;;
  *) echo "usage: $0 start|stop|status" >&2; exit 2 ;;
esac
