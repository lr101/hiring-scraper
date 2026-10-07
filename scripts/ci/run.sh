#!/usr/bin/env bash
# Build and test real images; dispose only this invocation's Compose resources.
set -euo pipefail
repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$repo_dir"
wait_timeout=${HIRING_CI_WAIT_TIMEOUT:-300}
if [[ ! "$wait_timeout" =~ ^[1-9][0-9]*$ ]]; then
    printf '%s\n' 'HIRING_CI_WAIT_TIMEOUT must be a positive whole number of seconds' >&2
    exit 2
fi
command -v docker >/dev/null
docker compose version
docker info >/dev/null

umask 077
private_dir=$(mktemp -d)
project="hiring-ci-$(date +%s)-$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')"
results_dir="$repo_dir/.ci-results"
mkdir -p "$results_dir"
env_file="$private_dir/ci.env"
HIRING_DOTENV_FILE="$env_file"
# Explicit environment overrides prevent a developer's credentials/images from
# winning Compose interpolation over this invocation's private generated values.
export POSTGRES_PASSWORD HIRING_DB_PASSWORD HIRING_APP_IMAGE HIRING_QUALITY_IMAGE HIRING_DOTENV_FILE
export APP_BIND APP_PORT
POSTGRES_PASSWORD=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
HIRING_DB_PASSWORD=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')
HIRING_APP_IMAGE="$project-app:check"
HIRING_QUALITY_IMAGE="$project-checks:check"
APP_BIND=127.0.0.1
APP_PORT=0
printf 'POSTGRES_PASSWORD=%s\nHIRING_DB_PASSWORD=%s\n' \
    "$POSTGRES_PASSWORD" "$HIRING_DB_PASSWORD" > "$env_file"
compose=(docker compose --env-file "$env_file" --project-name "$project" \
    --file compose.yaml --file compose.ci.yaml)

cleanup() {
    local status=$1 container_id
    trap - EXIT
    set +e
    "${compose[@]}" ps --all > "$results_dir/$project-services.log" 2>&1
    "${compose[@]}" logs --no-color > "$results_dir/$project-stack.log" 2>&1
    while IFS= read -r container_id; do
        [[ -n "$container_id" ]] || continue
        docker inspect --format '{{.Name}} {{json .State.Health}}' "$container_id"
    done < <("${compose[@]}" ps --all --quiet) > "$results_dir/$project-health.log" 2>&1
    if ! "${compose[@]}" down --volumes --remove-orphans; then
        printf '%s\n' "Cleanup failed for this invocation's project: $project" >&2
        status=1
    fi
    rm -rf "$private_dir"
    printf 'Compose project: %s\nExit status: %s\n' "$project" "$status" \
        > "$results_dir/$project-summary.txt"
    exit "$status"
}
trap 'cleanup $?' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

"${compose[@]}" config --quiet
"${compose[@]}" build --pull quality app 2>&1 | tee "$results_dir/$project-build.log"
"${compose[@]}" run --rm --no-deps quality 2>&1 | tee "$results_dir/$project-checks.log"
# The CI override disables live crawl workers while keeping the production app image.
"${compose[@]}" up --detach --wait --wait-timeout "$wait_timeout" app
"${compose[@]}" run --rm --no-deps smoke 2>&1 | tee "$results_dir/$project-smoke.log"
printf '%s\n' "Container checks passed; logs: $results_dir"
