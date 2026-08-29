#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
project_root=$(CDPATH= cd -- "$script_dir/.." && pwd)

if [ -x "$HOME/.dpm/bin/dpm" ]; then
  PATH="$HOME/.dpm/bin:$PATH"
  export PATH
fi
if [ -d /opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home ]; then
  JAVA_HOME=${JAVA_HOME:-/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home}
  PATH="/opt/homebrew/opt/openjdk@17/bin:$PATH"
  export JAVA_HOME PATH
fi

cd "$project_root"

if [ -n "${C8_IDP:-}" ]; then
  exec python3 scripts/wallet_cli.py run-demo
fi

json_port=${C8_JSON_PORT:-7575}
local_base="http://127.0.0.1:$json_port"
configured_base=${C8_BASE:-}
C8_BASE=${C8_BASE:-$local_base}
C8_USER=${C8_USER:-participant_admin}
C8_ADMIN_USER=${C8_ADMIN_USER:-participant_admin}
export C8_BASE C8_USER C8_ADMIN_USER

if python3 scripts/wallet_cli.py health >/dev/null 2>&1; then
  exec python3 scripts/wallet_cli.py run-demo
fi

if [ -n "$configured_base" ] && \
   [ "$configured_base" != "$local_base" ] && \
   [ "$configured_base" != "http://localhost:$json_port" ]; then
  printf '%s\n' '{"schemaVersion":1,"ok":false,"command":"run-demo","mode":"SANDBOX","error":{"category":"TARGET_UNREACHABLE","message":"The explicitly configured sandbox JSON API target is unavailable."}}'
  exit 1
fi

command -v dpm >/dev/null 2>&1 || {
  printf '%s\n' '{"schemaVersion":1,"ok":false,"command":"run-demo","mode":"SANDBOX","error":{"category":"DPM_NOT_FOUND","message":"DPM is required to start the sandbox fallback."}}'
  exit 1
}

if [ -z "${WALLET_DAR:-}" ]; then
  if ! dpm build >/dev/null 2>&1; then
    printf '%s\n' '{"schemaVersion":1,"ok":false,"command":"run-demo","mode":"SANDBOX","error":{"category":"BUILD_FAILED","message":"DPM could not build the business DAR."}}'
    exit 1
  fi
fi
temporary_dir=$(mktemp -d "${TMPDIR:-/tmp}/wallet-sandbox.XXXXXX")
sandbox_pid=""
cleanup() {
  if [ -n "$sandbox_pid" ]; then
    kill "$sandbox_pid" >/dev/null 2>&1 || true
    wait "$sandbox_pid" >/dev/null 2>&1 || true
  fi
  rm -rf "$temporary_dir"
}
trap cleanup EXIT HUP INT TERM

dpm sandbox \
  --ledger-api-port "${C8_LEDGER_PORT:-6865}" \
  --admin-api-port "${C8_ADMIN_PORT:-6866}" \
  --sequencer-public-port "${C8_SEQUENCER_PUBLIC_PORT:-6867}" \
  --sequencer-admin-port "${C8_SEQUENCER_ADMIN_PORT:-6868}" \
  --mediator-admin-port "${C8_MEDIATOR_ADMIN_PORT:-6869}" \
  --json-api-port "$json_port" \
  --log-file-appender off \
  --log-level-stdout WARN \
  >"$temporary_dir/sandbox.log" 2>&1 &
sandbox_pid=$!

attempt=0
while [ "$attempt" -lt 120 ]; do
  if python3 scripts/wallet_cli.py health >/dev/null 2>&1; then
    python3 scripts/wallet_cli.py run-demo
    exit $?
  fi
  if ! kill -0 "$sandbox_pid" >/dev/null 2>&1; then
    printf '%s\n' '{"schemaVersion":1,"ok":false,"command":"run-demo","mode":"SANDBOX","error":{"category":"SANDBOX_START_FAILED","message":"The DPM sandbox stopped before its JSON API became ready."}}'
    exit 1
  fi
  attempt=$((attempt + 1))
  sleep 0.5
done

printf '%s\n' '{"schemaVersion":1,"ok":false,"command":"run-demo","mode":"SANDBOX","error":{"category":"SANDBOX_START_TIMEOUT","message":"The DPM sandbox JSON API did not become ready in time."}}'
exit 1
