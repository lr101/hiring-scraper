#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage:
  preview.sh start --slug SLUG [--domain-suffix dev.dell.lr-projects.de]
                    [--gateway-port 18080] [--max-seconds 86400]
                    [--repo-root PATH]
  preview.sh status --slug SLUG
  preview.sh stop --slug SLUG

The preview uses a per-slug SQLite database and keeps runtime files under
/tmp/serve-dev-worktree/hiring-scraper by default.
EOF
}

die() { printf 'hiring-scraper preview: %s\n' "$*" >&2; exit 1; }

[[ $# -gt 0 ]] || { usage >&2; exit 2; }
action=$1
shift
slug=
domain_suffix=${DEV_DOMAIN_SUFFIX:-dev.dell.lr-projects.de}
gateway_port=${DEV_NGINX_PORT:-18080}
max_seconds=${DEV_STACK_MAX_SECONDS:-86400}
repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)

while (($#)); do
  case $1 in
    --slug) (($# >= 2)) || die '--slug needs a value'; slug=$2; shift 2 ;;
    --domain-suffix) (($# >= 2)) || die '--domain-suffix needs a value'; domain_suffix=$2; shift 2 ;;
    --gateway-port) (($# >= 2)) || die '--gateway-port needs a value'; gateway_port=$2; shift 2 ;;
    --max-seconds) (($# >= 2)) || die '--max-seconds needs a value'; max_seconds=$2; shift 2 ;;
    --repo-root) (($# >= 2)) || die '--repo-root needs a path'; repo_root=$(cd -- "$2" && pwd -P) || die "repository not found: $2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown option: $1" ;;
  esac
done

case "$action" in start|status|stop) ;; *) usage >&2; die "unknown action: $action" ;; esac
[[ "$slug" =~ ^[a-z0-9]([a-z0-9-]{0,46}[a-z0-9])?$ ]] || die 'slug must be lowercase DNS-safe text, up to 48 characters'

runtime_root=${HIRING_PREVIEW_STATE_ROOT:-/tmp/serve-dev-worktree/hiring-scraper}
nginx_runtime=${DEV_NGINX_RUNTIME_DIR:-/tmp/serve-dev-worktree/nginx}
port_state_dir=${DEV_PORT_STATE_DIR:-/tmp/serve-dev-worktree/ports}
state_dir="$runtime_root/$slug"
state_file="$state_dir/preview.state"
api_host="api-$slug.$domain_suffix"
web_host="web-$slug.$domain_suffix"
public_scheme=${DEV_PUBLIC_SCHEME:-https}
api_url="$public_scheme://$api_host"
web_url="$public_scheme://$web_host"
nginx_config="$nginx_runtime/nginx.conf"
route_dir="$nginx_runtime/worktrees"
route_file="$route_dir/$slug.conf"
route_owner="$route_dir/$slug.owner"
nginx_lock_file="$nginx_runtime/nginx.lock"
port_lock_file="$port_state_dir/.lock"

[[ "$runtime_root" == /* && "$nginx_runtime" == /* && "$port_state_dir" == /* ]] || die 'runtime directories must be absolute paths'
case "$runtime_root$nginx_runtime$port_state_dir" in *[!A-Za-z0-9_./-]*) die 'runtime directories contain unsupported characters' ;; esac
case "$domain_suffix" in ''|*[!A-Za-z0-9.-]*|.*|*.|*..*) die 'domain suffix is invalid' ;; esac
[[ "$gateway_port" == 18080 ]] || die 'shared preview gateway must use port 18080'
[[ "$max_seconds" =~ ^[0-9]+$ ]] && ((max_seconds > 0 && max_seconds <= 86400)) || die 'max-seconds must be between 1 and 86400'
case "$public_scheme" in http|https) ;; *) die 'DEV_PUBLIC_SCHEME must be http or https' ;; esac

if [[ "$action" == start && "${HIRING_PREVIEW_TIMEOUT_CHILD:-no}" != yes ]]; then
  command -v timeout >/dev/null 2>&1 || die 'required command is missing: timeout'
  cleanup_budget=30
  if ((max_seconds <= cleanup_budget)); then cleanup_budget=$((max_seconds - 1)); fi
  run_seconds=$((max_seconds - cleanup_budget))
  exec env HIRING_PREVIEW_TIMEOUT_CHILD=yes timeout --signal=TERM --kill-after="${cleanup_budget}s" \
    "${run_seconds}s" "$0" start --slug "$slug" --domain-suffix "$domain_suffix" \
    --gateway-port "$gateway_port" --max-seconds "$max_seconds" --repo-root "$repo_root"
fi

state_status=unknown
state_revision=
state_dirty=
state_owner_pid=
state_token=
state_api_pid=
state_web_pid=
state_worker_pid=
state_api_port=
state_web_port=
state_database=
state_public_verified=no
state_api_url=
state_web_url=
state_api_host=
state_web_host=
state_gateway_port=

load_state() {
  [[ -f "$state_file" ]] || return 1
  while IFS='=' read -r key value; do
    case "$key" in
      status) state_status=$value ;;
      revision) state_revision=$value ;;
      dirty) state_dirty=$value ;;
      owner_pid) state_owner_pid=$value ;;
      route_token) state_token=$value ;;
      api_pid) state_api_pid=$value ;;
      web_pid) state_web_pid=$value ;;
      worker_pid) state_worker_pid=$value ;;
      api_port) state_api_port=$value ;;
      web_port) state_web_port=$value ;;
      database) state_database=$value ;;
      public_verified) state_public_verified=$value ;;
      api_url) state_api_url=$value ;;
      web_url) state_web_url=$value ;;
      api_host) state_api_host=$value ;;
      web_host) state_web_host=$value ;;
      gateway_port) state_gateway_port=$value ;;
    esac
  done < "$state_file"
  [[ -z "$state_api_url" ]] || api_url=$state_api_url
  [[ -z "$state_web_url" ]] || web_url=$state_web_url
  [[ -z "$state_api_host" ]] || api_host=$state_api_host
  [[ -z "$state_web_host" ]] || web_host=$state_web_host
  [[ -z "$state_gateway_port" ]] || gateway_port=$state_gateway_port
}

write_state() {
  local status_value=$1 temporary="$state_file.tmp.$$"
  umask 077
  {
    printf 'status=%s\n' "$status_value"
    printf 'revision=%s\n' "$revision"
    printf 'dirty=%s\n' "$dirty"
    printf 'owner_pid=%s\n' "$state_owner_pid"
    printf 'route_token=%s\n' "$route_token"
    printf 'api_pid=%s\n' "$api_pid"
    printf 'web_pid=%s\n' "$web_pid"
    printf 'worker_pid=%s\n' "$worker_pid"
    printf 'api_port=%s\n' "$api_port"
    printf 'web_port=%s\n' "$web_port"
    printf 'database=%s\n' "$database_path"
    printf 'public_verified=%s\n' "$public_verified"
    printf 'api_url=%s\n' "$api_url"
    printf 'web_url=%s\n' "$web_url"
    printf 'api_host=%s\n' "$api_host"
    printf 'web_host=%s\n' "$web_host"
    printf 'gateway_port=%s\n' "$gateway_port"
  } > "$temporary"
  mv -- "$temporary" "$state_file"
}

process_group_matches() {
  local process_pid=$1 kind=$2 group command_line
  [[ "$process_pid" =~ ^[0-9]+$ && -r "/proc/$process_pid/cmdline" ]] || return 1
  group=$(ps -o pgid= -p "$process_pid" 2>/dev/null | tr -d ' ')
  [[ "$group" == "$process_pid" ]] || return 1
  command_line=$(tr '\0' ' ' < "/proc/$process_pid/cmdline" 2>/dev/null || true)
  case "$kind" in
    api) [[ "$command_line" == *"hiring_scraper.app.api:app"* ]] ;;
    web) [[ "$command_line" == *"vite"* && "$command_line" == *"preview"* ]] ;;
    worker) [[ "$command_line" == *"hiring_scraper.app.discovery_worker"* ]] ;;
    *) return 1 ;;
  esac
}

terminate_group() {
  local process_pid=$1 kind=$2
  process_group_matches "$process_pid" "$kind" || return 0
  kill -TERM -- "-$process_pid" >/dev/null 2>&1 || true
  for _ in $(seq 1 60); do
    process_group_matches "$process_pid" "$kind" || return 0
    sleep 0.1
  done
  kill -KILL -- "-$process_pid" >/dev/null 2>&1 || true
}

release_port_reservations() {
  [[ -d "$port_state_dir" ]] || return 0
  local reservation port owner
  exec {port_lock_fd}>"$port_lock_file"
  flock "$port_lock_fd"
  for port in "$state_api_port" "$state_web_port"; do
    [[ "$port" =~ ^[0-9]+$ ]] || continue
    reservation="$port_state_dir/$port"
    [[ -f "$reservation" ]] || continue
    read -r owner _ < "$reservation" || continue
    [[ "$owner" == "$state_owner_pid" ]] && python3 - "$reservation" "$state_owner_pid" "$slug" <<'PY'
from pathlib import Path
import sys

path, owner, slug = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
try:
    fields = path.read_text().split()
except OSError:
    raise SystemExit(0)
if fields == [owner, slug]:
    path.unlink(missing_ok=True)
PY
  done
  flock -u "$port_lock_fd"
}

remove_route() {
  local expected_token=$1 owner
  [[ -d "$route_dir" && -f "$route_owner" ]] || return 0
  exec {nginx_lock_fd}>"$nginx_lock_file"
  flock "$nginx_lock_fd"
  owner=$(cat "$route_owner" 2>/dev/null || true)
  if [[ "$owner" == "$expected_token" ]]; then
    python3 - "$route_file" "$route_owner" <<'PY'
from pathlib import Path
import sys
for raw_path in sys.argv[1:]:
    Path(raw_path).unlink(missing_ok=True)
PY
    if [[ -f "$nginx_config" ]] && nginx -t -p "$nginx_runtime" -c "$nginx_config" >/dev/null 2>&1; then
      nginx -s reload -p "$nginx_runtime" -c "$nginx_config" >/dev/null 2>&1 || true
    fi
  fi
  flock -u "$nginx_lock_fd"
}

if [[ "$action" == status ]]; then
  if ! load_state; then
    printf 'PREVIEW_STATE %s absent\n' "$slug"
    exit 0
  fi
  printf 'PREVIEW_STATE %s %s\n' "$slug" "$state_status"
  printf 'PREVIEW_REVISION %s dirty=%s\n' "$state_revision" "${state_dirty:-unknown}"
  if [[ "$state_status" == running ]]; then
    if process_group_matches "$state_api_pid" api && process_group_matches "$state_web_pid" web; then
      if [[ -n "$state_worker_pid" ]] && process_group_matches "$state_worker_pid" worker; then
        printf 'PREVIEW_SERVICE discovery-worker running (location searches only)\n'
      else
        printf 'PREVIEW_SERVICE discovery-worker not running\n'
      fi
      if [[ "$state_public_verified" == yes ]]; then
        printf 'PREVIEW_URL web %s/\n' "$web_url"
        printf 'PREVIEW_URL api %s/docs\n' "$api_url"
      else
        printf 'PREVIEW_GATEWAY web http://127.0.0.1:%s/ Host: %s\n' "$gateway_port" "$web_host"
        printf 'PREVIEW_GATEWAY api http://127.0.0.1:%s/health/ready Host: %s\n' "$gateway_port" "$api_host"
      fi
    else
      printf 'PREVIEW_STATE %s unhealthy\n' "$slug"
      exit 1
    fi
  else
    printf 'PREVIEW_STATE_DATABASE %s\n' "$state_database"
  fi
  exit 0
fi

if [[ "$action" == stop ]]; then
  if ! load_state; then
    printf 'PREVIEW_STATE %s absent\n' "$slug"
    exit 0
  fi
  revision=$state_revision
  dirty=$state_dirty
  route_token=$state_token
  api_pid=$state_api_pid
  web_pid=$state_web_pid
  worker_pid=$state_worker_pid
  api_port=$state_api_port
  web_port=$state_web_port
  database_path=$state_database
  public_verified=$state_public_verified
  write_state stopping
  terminate_group "$state_worker_pid" worker
  terminate_group "$state_api_pid" api
  terminate_group "$state_web_pid" web
  remove_route "$state_token"
  release_port_reservations
  revision=$state_revision
  dirty=$state_dirty
  route_token=$state_token
  api_pid=
  web_pid=
  worker_pid=
  api_port=$state_api_port
  web_port=$state_web_port
  database_path=$state_database
  public_verified=no
  write_state stopped
  printf 'PREVIEW_STATE %s stopped\n' "$slug"
  exit 0
fi

[[ -f "$repo_root/pyproject.toml" && -f "$repo_root/frontend/package.json" ]] || die "project files are missing under $repo_root"
for required_command in mise npm nginx curl flock setsid python3 ps; do
  command -v "$required_command" >/dev/null 2>&1 || die "required command is missing: $required_command"
done
[[ -f "$nginx_config" && -d "$route_dir" ]] || die "shared preview nginx is not initialized under $nginx_runtime"
[[ -x "$repo_root/frontend/node_modules/.bin/vite" ]] || (cd "$repo_root/frontend" && npm ci)

[[ ! -L "$runtime_root" ]] || die 'preview state root must not be a symlink'
mkdir -p -- "$runtime_root"
[[ ! -e "$state_dir" && ! -L "$state_dir" ]] || die "slug state already exists; choose a fresh slug: $slug"
mkdir -m 700 -- "$state_dir"
[[ ! -e "$state_dir/hiring.sqlite3" && ! -L "$state_dir/hiring.sqlite3" ]] || die 'slug database path is already in use'
[[ ! -e "$route_file" && ! -e "$route_owner" ]] || die "slug gateway route already exists: $slug"
for conf in "$route_dir"/*.conf; do
  [[ -f "$conf" ]] || continue
  if grep -Fq "server_name $api_host;" "$conf" || grep -Fq "server_name $web_host;" "$conf"; then
    die "gateway host is already routed by $conf"
  fi
done

umask 077
database_path="$state_dir/hiring.sqlite3"
database_url="sqlite:////${database_path#/}"
revision=$(git -C "$repo_root" rev-parse --short HEAD 2>/dev/null || printf unknown)
if [[ -n "$(git -C "$repo_root" status --porcelain 2>/dev/null || true)" ]]; then dirty=yes; else dirty=no; fi
route_token="$slug-$$-$RANDOM"
state_owner_pid=${BASHPID:-$$}
api_pid=
web_pid=
worker_pid=
api_port=
web_port=
api_port_marker=
web_port_marker=
public_verified=no
adapter_state=starting
registered_route=no
route_claimed=no
cleanup_done=no
adapter_started_at=$(date +%s)
exec {slug_lock_fd}>"$state_dir/runtime.lock"
flock -n "$slug_lock_fd" || die "slug is already active: $slug"

write_state starting

port_is_free() {
  python3 - "$1" <<'PY'
import socket
import sys

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", int(sys.argv[1])))
PY
}

reserve_port() {
  local role=$1 candidate marker owner
  exec {port_lock_fd}>"$port_lock_file"
  flock "$port_lock_fd"
  mkdir -p -- "$port_state_dir"
  for _ in $(seq 1 100); do
    candidate=$(python3 - <<'PY'
import socket
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)
    marker="$port_state_dir/$candidate"
    if [[ -e "$marker" ]]; then
      read -r owner _ < "$marker" || owner=
      if [[ "$owner" =~ ^[0-9]+$ ]] && kill -0 "$owner" >/dev/null 2>&1; then continue; fi
      python3 - "$marker" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[1]).unlink(missing_ok=True)
PY
    fi
    if port_is_free "$candidate"; then
      printf '%s %s\n' "$state_owner_pid" "$slug" > "$marker"
      case "$role" in
        api) api_port=$candidate; api_port_marker=$marker ;;
        web) web_port=$candidate; web_port_marker=$marker ;;
      esac
      flock -u "$port_lock_fd"
      return 0
    fi
  done
  flock -u "$port_lock_fd"
  die "could not reserve a free $role port"
}

write_state starting

cleanup() {
  local exit_code=$?
  [[ "$cleanup_done" == yes ]] && exit "$exit_code"
  cleanup_done=yes
  trap - EXIT INT TERM HUP
  set +e
  if [[ -n "$worker_pid" ]]; then terminate_group "$worker_pid" worker; fi
  if [[ -n "$api_pid" ]]; then terminate_group "$api_pid" api; fi
  if [[ -n "$web_pid" ]]; then terminate_group "$web_pid" web; fi
  if [[ "$registered_route" == yes || "$route_claimed" == yes ]]; then remove_route "$route_token"; fi
  release_port_reservations
  if [[ "$adapter_state" == starting ]]; then adapter_state=failed; fi
  write_state "$adapter_state"
  exit "$exit_code"
}
on_signal() { adapter_state=stopped; exit 130; }
trap cleanup EXIT
trap on_signal INT TERM HUP

reserve_port api
reserve_port web
write_state starting

printf 'Setting up the isolated fixture and production UI bundle...\n'
(cd "$repo_root" && mise exec -- uv sync --locked)
(cd "$repo_root" && DATABASE_URL="$database_url" mise exec -- uv run hiring-seed)
  (cd "$repo_root/frontend" && VITE_API_BASE_URL="$api_url" npm run build -- --outDir "$state_dir/web" --emptyOutDir)

api_log="$state_dir/api.log"
web_log="$state_dir/web.log"
worker_log="$state_dir/discovery-worker.log"
: > "$api_log"
: > "$web_log"
: > "$worker_log"
chmod 600 "$api_log" "$web_log" "$worker_log"

(
  cd "$repo_root"
  exec setsid env DATABASE_URL="$database_url" CORS_ORIGINS="$web_url" \
    mise exec -- uv run uvicorn hiring_scraper.app.api:app --host 127.0.0.1 --port "$api_port"
) >> "$api_log" 2>&1 < /dev/null &
api_pid=$!
(
  cd "$repo_root/frontend"
  exec setsid env HIRING_PREVIEW_WEB_HOST="$web_host" ./node_modules/.bin/vite preview \
    --host 127.0.0.1 --port "$web_port" --strictPort --outDir "$state_dir/web"
) >> "$web_log" 2>&1 < /dev/null &
web_pid=$!
write_state starting

wait_http() {
  local url=$1 process_pid=$2 kind=$3
  for _ in $(seq 1 120); do
    if curl --silent --show-error --fail --max-time 2 "$url" >/dev/null 2>&1; then return 0; fi
    process_group_matches "$process_pid" "$kind" || return 1
    sleep 1
  done
  return 1
}

if ! wait_http "http://127.0.0.1:$api_port/health/ready" "$api_pid" api; then
  tail -n 80 "$api_log" >&2 || true
  die 'API did not become ready'
fi
if ! wait_http "http://127.0.0.1:$web_port/" "$web_pid" web; then
  tail -n 80 "$web_log" >&2 || true
  die 'web UI did not become ready'
fi

register_route() {
  local temporary="$route_file.tmp.$$" collision
  exec {nginx_lock_fd}>"$nginx_lock_file"
  flock "$nginx_lock_fd"
  [[ ! -e "$route_file" && ! -e "$route_owner" ]] || die "slug gateway route already exists: $slug"
  for collision in "$route_dir"/*.conf; do
    [[ -f "$collision" ]] || continue
    if grep -Fq "server_name $api_host;" "$collision" || grep -Fq "server_name $web_host;" "$collision"; then
      die "gateway host is already routed by $collision"
    fi
  done
  cat > "$temporary" <<EOF
server {
    listen $gateway_port;
    server_name $api_host;
    location / {
        proxy_pass http://127.0.0.1:$api_port;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-Proto \$dev_forwarded_proto;
        proxy_set_header Connection "";
    }
}
server {
    listen $gateway_port;
    server_name $web_host;
    location / {
        proxy_pass http://127.0.0.1:$web_port;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-Proto \$dev_forwarded_proto;
        proxy_set_header Connection "";
    }
}
EOF
  route_claimed=yes
  printf '%s\n' "$route_token" > "$route_owner"
  mv -- "$temporary" "$route_file"
  if ! nginx -t -p "$nginx_runtime" -c "$nginx_config" >/dev/null; then
    python3 - "$route_file" "$route_owner" <<'PY'
from pathlib import Path
import sys
for p in sys.argv[1:]: Path(p).unlink(missing_ok=True)
PY
    route_claimed=no
    flock -u "$nginx_lock_fd"
    die 'the shared nginx rejected the preview route'
  fi
  if ! nginx -s reload -p "$nginx_runtime" -c "$nginx_config" >/dev/null; then
    python3 - "$route_file" "$route_owner" <<'PY'
from pathlib import Path
import sys
for p in sys.argv[1:]: Path(p).unlink(missing_ok=True)
PY
    route_claimed=no
    flock -u "$nginx_lock_fd"
    die 'the shared nginx could not reload the preview route'
  fi
  registered_route=yes
  flock -u "$nginx_lock_fd"
}

register_route
(
  cd "$repo_root"
  exec setsid env DATABASE_URL="$database_url" CAREER_DISCOVERY_POLL_SECONDS=3 \
    CAREER_DISCOVERY_WORKERS="${CAREER_DISCOVERY_WORKERS:-4}" \
    CAREER_DISCOVERY_CAPTURE_DIR="$state_dir/discovery-captures" \
    mise exec -- uv run python -m hiring_scraper.app.discovery_worker --location-jobs-only
) >> "$worker_log" 2>&1 < /dev/null &
worker_pid=$!
write_state starting

worker_ready=no
for _ in $(seq 1 40); do
  if ! process_group_matches "$worker_pid" worker; then
    tail -n 80 "$worker_log" >&2 || true
    die 'discovery worker exited during startup'
  fi
  if grep -Fq 'location_jobs_only=True' "$worker_log"; then
    worker_ready=yes
    break
  fi
  sleep 0.25
done
if [[ "$worker_ready" != yes ]]; then
  tail -n 80 "$worker_log" >&2 || true
  die 'discovery worker did not become ready'
fi

write_state running
wait_gateway() {
  local host=$1 path=$2
  for _ in $(seq 1 40); do
    if curl --silent --show-error --fail --max-time 2 -H "Host: $host" \
      "http://127.0.0.1:$gateway_port$path" >/dev/null 2>&1; then return 0; fi
    sleep 0.25
  done
  return 1
}
wait_gateway "$web_host" / || die 'web route through the shared gateway did not respond'
wait_gateway "$api_host" /health/ready || die 'API route through the shared gateway did not respond'

if curl --silent --show-error --fail --connect-timeout 4 --max-time 12 "$web_url/" >/dev/null 2>&1 && \
   curl --silent --show-error --fail --connect-timeout 4 --max-time 12 "$api_url/health/ready" >/dev/null 2>&1; then
  public_verified=yes
else
  public_verified=no
fi
write_state running

if [[ "$public_verified" == yes ]]; then
  printf 'PREVIEW_URL web %s/\n' "$web_url"
  printf 'PREVIEW_URL api %s/docs\n' "$api_url"
else
  printf 'PUBLIC_ROUTE_UNVERIFIED DNS/TLS/Traefik could not reach both public hosts.\n'
  printf 'PREVIEW_GATEWAY web http://127.0.0.1:%s/ Host: %s\n' "$gateway_port" "$web_host"
  printf 'PREVIEW_GATEWAY api http://127.0.0.1:%s/docs Host: %s\n' "$gateway_port" "$api_host"
fi
printf 'PREVIEW_REVISION %s dirty=%s\n' "$revision" "$dirty"
printf 'PREVIEW_STATE_DIR %s\n' "$state_dir"

while true; do
  if load_state && [[ "$state_status" == stopping || "$state_status" == stopped ]]; then
    adapter_state=stopped
    break
  fi
  if ! process_group_matches "$api_pid" api || ! process_group_matches "$web_pid" web || \
     ! process_group_matches "$worker_pid" worker; then
    adapter_state=failed
    die 'a preview service exited unexpectedly; inspect api.log, web.log, or discovery-worker.log in the state directory'
  fi
  now=$(date +%s)
  if ((now - adapter_started_at >= max_seconds)); then
    adapter_state=stopped
    break
  fi
  sleep 2
done
