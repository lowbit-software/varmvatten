#!/usr/bin/env bash
#
# Pull-based deploy agent. Run from a systemd timer (see DEPLOY.md).
#
# Checks GHCR for the image currently tagged :stable. If it differs from what
# is deployed, pins the new digest in .deploy.env, restarts the stack, and
# waits for it to report healthy. If it does not come up, rolls back to the
# previous digest and quarantines the bad one so the timer does not flap.
#
# Nothing here reaches out to GitHub except an outbound registry pull, so the
# box needs no inbound ports.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

# DEPLOY_IMAGE/DEPLOY_CHANNEL exist so this can be rehearsed against a scratch
# registry without touching the real one. Production overrides neither.
IMAGE="${DEPLOY_IMAGE:-ghcr.io/lowbit-software/varmvatten}"
CHANNEL="${DEPLOY_CHANNEL:-stable}"
STATE_FILE="$REPO_DIR/.deploy.env"
QUARANTINE_FILE="$REPO_DIR/.deploy.failed"
HEALTH_URL="http://127.0.0.1:5000/healthz"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-90}"

log() { printf '%s  %s\n' "$(date -Is)" "$*"; }
fail() { log "ERROR: $*" >&2; exit 1; }

# Overlapping runs would fight over .deploy.env. A skipped tick is harmless —
# the timer comes back in five minutes.
exec 9>"$REPO_DIR/.deploy.lock"
if ! flock -n 9; then
  log "another deploy is in progress, skipping"
  exit 0
fi

compose() {
  local args=(--env-file "$REPO_DIR/.env")
  if [ -f "$STATE_FILE" ]; then
    args+=(--env-file "$STATE_FILE")
  fi
  docker compose "${args[@]}" "$@"
}

read_state() {
  [ -f "$STATE_FILE" ] || return 0
  sed -n 's/^APP_IMAGE=//p' "$STATE_FILE" | tail -1
}

write_state() {
  printf '# Written by scripts/deploy.sh — the exact image this host runs.\nAPP_IMAGE=%s\n' \
    "$1" > "$STATE_FILE"
}

# Bring the stack up on whatever .deploy.env currently pins, and confirm it
# serves traffic on the published port (which the in-container healthcheck
# alone would not prove).
bring_up() {
  compose up -d --wait --wait-timeout "$HEALTH_TIMEOUT" || return 1
  local deadline=$(( SECONDS + HEALTH_TIMEOUT ))
  while (( SECONDS < deadline )); do
    if curl -fsS -o /dev/null --max-time 5 "$HEALTH_URL"; then
      return 0
    fi
    sleep 3
  done
  return 1
}

[ -f "$REPO_DIR/.env" ] || fail ".env missing — it holds CLOUDFLARE_TUNNEL_TOKEN and APP_UID/APP_GID"

log "checking $IMAGE:$CHANNEL"
if ! docker pull -q "$IMAGE:$CHANNEL" >/dev/null 2>&1; then
  fail "cannot pull $IMAGE:$CHANNEL — is the package published and public, or is a login needed? (see DEPLOY.md)"
fi

# Resolve the tag to an immutable digest so restarts can't drift underneath us.
new_ref="$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$IMAGE:$CHANNEL" \
           | grep -m1 "^${IMAGE}@" || true)"
[ -n "$new_ref" ] || fail "could not resolve a digest for $IMAGE:$CHANNEL"

current_ref="$(read_state)"

if [ "$new_ref" = "$current_ref" ] && [ -n "$(compose ps -q --status running webapp 2>/dev/null)" ]; then
  log "already on $new_ref, nothing to do"
  exit 0
fi

# A digest that already failed here stays quarantined until a different one is
# promoted — otherwise every tick would redeploy and roll back the same bad build.
if [ -f "$QUARANTINE_FILE" ] && [ "$new_ref" = "$(cat "$QUARANTINE_FILE")" ]; then
  log "$new_ref failed health checks previously — skipping until a new image is promoted"
  exit 0
fi

if [ -n "$current_ref" ]; then
  log "deploying $new_ref (from $current_ref)"
else
  log "deploying $new_ref (no previous digest recorded)"
fi

write_state "$new_ref"

if bring_up; then
  log "deployed and healthy: $new_ref"
  rm -f "$QUARANTINE_FILE"
  docker image prune -f --filter "until=168h" >/dev/null 2>&1 || true
  exit 0
fi

log "new image failed to become healthy" >&2
compose logs --tail 40 webapp >&2 || true
printf '%s\n' "$new_ref" > "$QUARANTINE_FILE"

if [ -z "$current_ref" ]; then
  rm -f "$STATE_FILE"
  fail "no previous digest to roll back to — stack is DOWN or unhealthy, fix manually"
fi

log "rolling back to $current_ref" >&2
write_state "$current_ref"
if bring_up; then
  fail "rolled back to $current_ref — $new_ref is bad and is now quarantined, do not re-promote it"
fi
fail "ROLLBACK FAILED — stack is unhealthy on $current_ref, manual intervention needed"
