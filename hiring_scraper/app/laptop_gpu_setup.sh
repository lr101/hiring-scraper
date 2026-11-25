#!/usr/bin/env bash
# Radius laptop GPU: serve the matching models from this laptop's NVIDIA GPU so the
# homelab can hand its local model work to it while the laptop is on the VPN.
#
#   bash radius-laptop-gpu.sh install   # once: llama.cpp (CUDA), pinned models, user services
#   bash radius-laptop-gpu.sh start     # serve; then turn on "Use laptop GPU" in Radius Settings
#   bash radius-laptop-gpu.sh stop      # free the GPU; Radius goes back to the homelab models
#   bash radius-laptop-gpu.sh status    # are both servers up, and how many slots
#   bash radius-laptop-gpu.sh logs      # follow both server logs
#   bash radius-laptop-gpu.sh autostart on|off   # start with your login session
#   bash radius-laptop-gpu.sh uninstall
#
# Needs Fedora (or another systemd Linux) with the NVIDIA driver working (nvidia-smi)
# and driver 570 or newer. The script brings its own CUDA runtime libraries, so a
# system CUDA toolkit is neither needed nor touched. Traffic is plain HTTP inside the VPN.
#
# Overrides (environment): BIND_ADDRESS (VPN IP to listen on), PARALLEL (postings at
# once; default from GPU memory), RERANK_DEVICE (gpu|cpu), RADIUS_GPU_HOME.
set -euo pipefail

# Filled in by Radius from Settings -> Matching model -> Laptop address.
CONFIGURED_ADDRESS='__RADIUS_LAPTOP_ADDRESS__'

# Pins: keep in step with compose.yaml and scripts/dev/local-models.sh.
LLAMA_BUILD=b12098
LLAMA_ASSET=llama-$LLAMA_BUILD-bin-ubuntu-cuda-12.8-x64.tar.gz
LLAMA_SHA256=00e1d8078f6184802ac03cae399fa66b5b07e263b3cdf176d1e55a217f1eab89
CUDART_ASSET=cudart-llama-$LLAMA_BUILD-bin-ubuntu-cuda-12.8-x64.tar.gz
CUDART_SHA256=2e8dc09d01b4a0d84f5ac2b1df46d035a626a43ee49ad3819a2785dc30400223
MIN_DRIVER=570
LLM_URL=https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/e87f176479d0855a907a41277aca2f8ee7a09523/Qwen3.5-4B-Q4_K_M.gguf
LLM_FILE=Qwen3.5-4B-Q4_K_M.gguf
LLM_SHA256=00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4
LLM_ALIAS='unsloth/Qwen3.5-4B-GGUF@e87f176479d0:Qwen3.5-4B-Q4_K_M.gguf'
RERANK_URL=https://huggingface.co/ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF/resolve/a02f48bb4f057028298c21fa033da2b30d7742d5/qwen3-reranker-0.6b-q8_0.gguf
RERANK_FILE=qwen3-reranker-0.6b-q8_0.gguf
RERANK_SHA256=22c9979ce4fbcdc5acdc310c6641c32797eff1aa980b8f7a2db8a8ea23429a48
RERANK_ALIAS='ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF@a02f48bb4f05:qwen3-reranker-0.6b-q8_0.gguf'
LLM_PORT=8091
RERANK_PORT=8092

HOME_DIR=${RADIUS_GPU_HOME:-$HOME/.local/share/radius-gpu}
BIN_DIR=$HOME_DIR/llama-$LLAMA_BUILD
UNIT_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
CONFIG=$HOME_DIR/config.env

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }

check_gpu() {
  if ! command -v nvidia-smi >/dev/null || ! nvidia-smi >/dev/null 2>&1; then
    fail 'nvidia-smi does not work. Install the NVIDIA driver first (RPM Fusion: akmod-nvidia, xorg-x11-drv-nvidia-cuda) and reboot.'
  fi
  local driver
  driver=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n1)
  (( ${driver%%.*} >= MIN_DRIVER )) || fail "NVIDIA driver $driver is too old for CUDA 12.8; update to $MIN_DRIVER or newer."
  say "GPU: $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader | head -n1), driver $driver"
}

download() {  # url file sha256
  local url=$1 file=$2 digest=$3
  if [[ -f $file ]] && [[ $(sha256sum "$file" | cut -d' ' -f1) == "$digest" ]]; then return; fi
  say "Downloading $(basename "$file")"
  curl -fL --retry 3 -C - -o "$file.part" "$url"
  [[ $(sha256sum "$file.part" | cut -d' ' -f1) == "$digest" ]] \
    || { rm -f "$file.part"; fail "$(basename "$file") does not match the pinned SHA-256; refusing to use it."; }
  mv "$file.part" "$file"
}

is_local_ip() { ip -o addr show | awk '{print $4}' | cut -d/ -f1 | grep -qxF "$1"; }

pick_address() {
  local candidate=${BIND_ADDRESS:-}
  if [[ -z $candidate && $CONFIGURED_ADDRESS != __RADIUS_* && -n $CONFIGURED_ADDRESS ]]; then
    candidate=$CONFIGURED_ADDRESS
    is_local_ip "$candidate" || candidate=$(getent ahosts "$CONFIGURED_ADDRESS" | awk '{print $1}' | while read -r ip; do
      is_local_ip "$ip" && { echo "$ip"; break; }; done || true)
  fi
  if [[ -z $candidate ]] && command -v tailscale >/dev/null; then candidate=$(tailscale ip -4 2>/dev/null | head -n1 || true); fi
  if [[ -z $candidate ]]; then
    read -rp 'VPN IP address of this laptop (the homelab connects to it): ' candidate
  fi
  [[ -n $candidate ]] || fail 'No VPN address given.'
  is_local_ip "$candidate" || say "Note: $candidate is not on an interface right now; the servers start once the VPN is up."
  BIND_ADDRESS=$candidate
}

pick_sizes() {
  local vram
  vram=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -n1)
  # The 4B model needs about 2.7 GB plus ~0.3 GB per parallel slot (8k tokens each);
  # llama.cpp's --fit keeps any layer that does not fit on the CPU instead of failing.
  # Its default 1 GiB safety margin pushes layers off a 4 GB card, so the LLM uses 256 MiB.
  if (( vram < 6000 )); then PARALLEL=${PARALLEL:-2}; RERANK_DEVICE=${RERANK_DEVICE:-cpu}
  elif (( vram < 10000 )); then PARALLEL=${PARALLEL:-3}; RERANK_DEVICE=${RERANK_DEVICE:-gpu}
  else PARALLEL=${PARALLEL:-4}; RERANK_DEVICE=${RERANK_DEVICE:-gpu}; fi
  [[ $PARALLEL =~ ^[1-8]$ ]] || fail 'PARALLEL must be 1-8.'
  [[ $RERANK_DEVICE == gpu || $RERANK_DEVICE == cpu ]] || fail 'RERANK_DEVICE must be gpu or cpu.'
  say "GPU memory ${vram} MiB: $PARALLEL postings at once, reranker on $RERANK_DEVICE"
}

write_units() {
  local cores rerank_device=() rerank_parallel=2
  cores=$(( $(nproc) / 2 )); (( cores >= 2 )) || cores=2
  if [[ $RERANK_DEVICE == cpu ]]; then rerank_device=(--device none); else rerank_parallel=$PARALLEL; fi
  mkdir -p "$UNIT_DIR"
  cat >"$UNIT_DIR/radius-llm.service" <<EOF
[Unit]
Description=Radius job analysis LLM (laptop GPU)
StartLimitIntervalSec=0

[Service]
Environment=LD_LIBRARY_PATH=$BIN_DIR
ExecStart=$BIN_DIR/llama-server --host $BIND_ADDRESS --port $LLM_PORT --model $HOME_DIR/models/$LLM_FILE --alias $LLM_ALIAS --parallel $PARALLEL --fit-target 256 --no-kv-unified --ctx-size $(( 8192 * PARALLEL )) --threads $cores --chat-template-kwargs "{\"enable_thinking\":false}" --no-webui
Restart=always
RestartSec=15

[Install]
WantedBy=default.target
EOF
  cat >"$UNIT_DIR/radius-reranker.service" <<EOF
[Unit]
Description=Radius CV evidence reranker (laptop)
After=radius-llm.service
StartLimitIntervalSec=0

[Service]
Environment=LD_LIBRARY_PATH=$BIN_DIR
ExecStart=$BIN_DIR/llama-server --host $BIND_ADDRESS --port $RERANK_PORT --model $HOME_DIR/models/$RERANK_FILE --alias $RERANK_ALIAS --reranking ${rerank_device[*]} --parallel $rerank_parallel --no-kv-unified --ctx-size $(( 4096 * rerank_parallel )) --batch-size 2048 --ubatch-size 2048 --threads $cores --no-webui
Restart=always
RestartSec=15

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
}

open_firewall() {
  command -v firewall-cmd >/dev/null && systemctl is-active --quiet firewalld || return 0
  local iface zone
  iface=$(ip -o addr show | awk -v ip="$BIND_ADDRESS" '{split($4, a, "/"); if (a[1] == ip) print $2}' | head -n1)
  zone=$( { [[ -n $iface ]] && firewall-cmd --get-zone-of-interface="$iface" 2>/dev/null; } || firewall-cmd --get-default-zone)
  if firewall-cmd --zone="$zone" --query-port=$LLM_PORT/tcp >/dev/null && firewall-cmd --zone="$zone" --query-port=$RERANK_PORT/tcp >/dev/null; then
    return
  fi
  say "Opening ports $LLM_PORT and $RERANK_PORT in firewalld zone '$zone' (sudo)"
  sudo firewall-cmd --permanent --zone="$zone" --add-port=$LLM_PORT/tcp --add-port=$RERANK_PORT/tcp >/dev/null
  sudo firewall-cmd --reload >/dev/null
  printf 'FIREWALL_ZONE=%q\n' "$zone" >>"$CONFIG"
}

install() {
  command -v systemctl >/dev/null || fail 'systemd is required.'
  check_gpu
  pick_address
  pick_sizes
  mkdir -p "$HOME_DIR/models"
  if [[ ! -x $BIN_DIR/llama-server ]]; then
    download "https://github.com/ggml-org/llama.cpp/releases/download/$LLAMA_BUILD/$LLAMA_ASSET" "$HOME_DIR/$LLAMA_ASSET" "$LLAMA_SHA256"
    download "https://github.com/ggml-org/llama.cpp/releases/download/$LLAMA_BUILD/$CUDART_ASSET" "$HOME_DIR/$CUDART_ASSET" "$CUDART_SHA256"
    rm -rf "$BIN_DIR.tmp" && mkdir -p "$BIN_DIR.tmp"
    tar xzf "$HOME_DIR/$LLAMA_ASSET" -C "$BIN_DIR.tmp" --strip-components=1
    tar xzf "$HOME_DIR/$CUDART_ASSET" -C "$BIN_DIR.tmp" --strip-components=1
    rm -rf "$BIN_DIR" && mv "$BIN_DIR.tmp" "$BIN_DIR"
    rm -f "$HOME_DIR/$LLAMA_ASSET" "$HOME_DIR/$CUDART_ASSET"
  fi
  download "$LLM_URL" "$HOME_DIR/models/$LLM_FILE" "$LLM_SHA256"
  download "$RERANK_URL" "$HOME_DIR/models/$RERANK_FILE" "$RERANK_SHA256"
  printf 'BIND_ADDRESS=%q\nPARALLEL=%q\nRERANK_DEVICE=%q\n' "$BIND_ADDRESS" "$PARALLEL" "$RERANK_DEVICE" >"$CONFIG"
  write_units
  open_firewall
  say "Installed. Start with: bash $0 start"
}

start() {
  [[ -f $UNIT_DIR/radius-llm.service ]] || install
  systemctl --user start radius-llm.service radius-reranker.service
  say 'Starting; loading the models takes up to a minute.'
  for _ in $(seq 60); do
    status quiet && break
    sleep 2
  done
  status
  say 'In Radius: Settings -> Matching model -> Local models -> turn on "Use laptop GPU".'
}

status() {
  [[ -f $CONFIG ]] || fail 'Not installed yet; run: bash radius-laptop-gpu.sh install'
  # shellcheck disable=SC1090
  source "$CONFIG"
  local up=0 port name
  for pair in "llm:$LLM_PORT" "reranker:$RERANK_PORT"; do
    name=${pair%%:*}; port=${pair##*:}
    local slots
    if slots=$(curl -fsS -m 3 "http://$BIND_ADDRESS:$port/props" 2>/dev/null | grep -o '"total_slots":[0-9]*' | cut -d: -f2) && [[ -n $slots ]]; then
      up=$((up + 1)); [[ ${1:-} == quiet ]] || echo "$name     http://$BIND_ADDRESS:$port  up, $slots parallel slots"
    else
      [[ ${1:-} == quiet ]] || echo "$name     http://$BIND_ADDRESS:$port  down"
    fi
  done
  [[ ${1:-} == quiet ]] || nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv,noheader \
    | awk '{print "GPU      " $0}'
  (( up == 2 ))
}

case "${1:-install}" in
  install) install ;;
  start) start ;;
  stop) systemctl --user stop radius-llm.service radius-reranker.service; say 'Stopped; Radius uses the homelab models again.' ;;
  status) status || true ;;
  logs) journalctl --user -f -u radius-llm.service -u radius-reranker.service ;;
  autostart)
    case "${2:-}" in
      on) systemctl --user enable radius-llm.service radius-reranker.service ;;
      off) systemctl --user disable radius-llm.service radius-reranker.service ;;
      *) fail 'usage: autostart on|off' ;;
    esac ;;
  uninstall)
    systemctl --user disable --now radius-llm.service radius-reranker.service 2>/dev/null || true
    rm -f "$UNIT_DIR/radius-llm.service" "$UNIT_DIR/radius-reranker.service"
    systemctl --user daemon-reload
    if [[ -f $CONFIG ]] && grep -q '^FIREWALL_ZONE=' "$CONFIG"; then
      # shellcheck disable=SC1090
      source "$CONFIG"
      sudo firewall-cmd --permanent --zone="$FIREWALL_ZONE" --remove-port=$LLM_PORT/tcp --remove-port=$RERANK_PORT/tcp >/dev/null || true
      sudo firewall-cmd --reload >/dev/null || true
    fi
    rm -rf "$HOME_DIR"
    say 'Removed the services, models and llama.cpp.' ;;
  *) sed -n '2,13p' "$0"; exit 2 ;;
esac
