#!/usr/bin/env bash

# Shared, source-only helpers for the root demo commands. Every path is
# anchored to this checkout so callers do not need an activated environment or
# a particular working directory.

DEMO_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
DEMO_ROOT="$(cd "$DEMO_SCRIPT_DIR/.." && pwd -P)"
DEMO_RUN_DIR="$DEMO_ROOT/.run"
DEMO_VENV_DIR="$DEMO_ROOT/.venv"
DEMO_PYTHON="$DEMO_VENV_DIR/bin/python"
DEMO_REQUIREMENTS="$DEMO_ROOT/requirements.txt"
DEMO_REQUIREMENTS_MARKER="$DEMO_VENV_DIR/.demo-requirements.sha256"

DEMO_COLLATERAL_DIR="$DEMO_ROOT/daml"
DEMO_COLLATERAL_DAR="$DEMO_COLLATERAL_DIR/.daml/dist/collateral-optimizer-0.0.1.dar"
DEMO_WALLET_DIR="$DEMO_ROOT/agent_wallet"
DEMO_WALLET_DAR="$DEMO_WALLET_DIR/.daml/dist/agent-wallet-0.0.1.dar"

# The root demo always owns a brand-new sandbox on these dedicated ports. In
# particular, it never probes, reuses, signals, or stops a service on 7575.
DEMO_LEDGER_HOST="127.0.0.1"
DEMO_LEDGER_GRPC_PORT="16865"
DEMO_LEDGER_ADMIN_PORT="16866"
DEMO_SEQUENCER_PUBLIC_PORT="16867"
DEMO_SEQUENCER_ADMIN_PORT="16868"
DEMO_MEDIATOR_ADMIN_PORT="16869"
DEMO_LEDGER_HTTP_PORT="17575"
DEMO_LEDGER_URL="http://$DEMO_LEDGER_HOST:$DEMO_LEDGER_HTTP_PORT"

DEMO_DPM_BIN="${DEMO_DPM_BIN:-}"
DEMO_SYSTEM_PYTHON=""
DEMO_CURL_BIN=""
DEMO_JAVA_HOME=""
DEMO_CANTON_PID=""
DEMO_CANTON_PGID=""
DEMO_CANTON_STARTED="0"
DEMO_CANTON_GROUP_READY="0"
DEMO_CANTON_PID_FILE="$DEMO_RUN_DIR/canton.pid"
DEMO_CANTON_GROUP_FILE="$DEMO_RUN_DIR/canton.process-group"
DEMO_CANTON_PORT_FILE="$DEMO_RUN_DIR/canton-ports.json"
DEMO_LOCK_HELD="0"

demo_fail() {
  printf 'ERROR: %s\n' "$*" >&2
  return 1
}

demo_find_system_python() {
  if command -v python3 >/dev/null 2>&1; then
    DEMO_SYSTEM_PYTHON="$(command -v python3)"
    return 0
  fi
  demo_fail "Python 3 was not found. Install Python 3, then rerun ./setup_demo.sh."
}

demo_configure_toolchain() {
  if [[ -z "$DEMO_DPM_BIN" ]]; then
    if [[ -n "${HOME:-}" && -x "$HOME/.dpm/bin/dpm" ]]; then
      DEMO_DPM_BIN="$HOME/.dpm/bin/dpm"
    elif command -v dpm >/dev/null 2>&1; then
      DEMO_DPM_BIN="$(command -v dpm)"
    fi
  fi
  if [[ -z "$DEMO_DPM_BIN" || ! -x "$DEMO_DPM_BIN" ]]; then
    demo_fail "DPM was not found. Expected an executable at \$HOME/.dpm/bin/dpm or on PATH."
    return 1
  fi

  if command -v curl >/dev/null 2>&1; then
    DEMO_CURL_BIN="$(command -v curl)"
  elif [[ -x /usr/bin/curl ]]; then
    DEMO_CURL_BIN="/usr/bin/curl"
  else
    demo_fail "curl is required for Canton readiness and package loading."
    return 1
  fi

  local java_candidate=""
  local brew_prefix=""
  if [[ -n "${JAVA_HOME:-}" && -x "$JAVA_HOME/bin/java" ]]; then
    java_candidate="$JAVA_HOME"
  fi
  if [[ -z "$java_candidate" ]]; then
    for java_candidate in \
      /opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home \
      /opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home \
      /usr/local/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home \
      /usr/local/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home
    do
      if [[ -x "$java_candidate/bin/java" ]]; then
        break
      fi
      java_candidate=""
    done
  fi
  if [[ -z "$java_candidate" ]] && command -v brew >/dev/null 2>&1; then
    brew_prefix="$(brew --prefix openjdk@17 2>/dev/null || true)"
    if [[ -n "$brew_prefix" && -x "$brew_prefix/libexec/openjdk.jdk/Contents/Home/bin/java" ]]; then
      java_candidate="$brew_prefix/libexec/openjdk.jdk/Contents/Home"
    fi
  fi
  if [[ -z "$java_candidate" ]]; then
    demo_fail "Java was not found. Expected Homebrew OpenJDK 17 or 21."
    return 1
  fi

  DEMO_JAVA_HOME="$java_candidate"
  export JAVA_HOME="$DEMO_JAVA_HOME"
  export PATH="$(dirname "$DEMO_DPM_BIN"):$DEMO_JAVA_HOME/bin:$PATH"
}

demo_sha256() {
  local target_file="$1"
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$target_file" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$target_file" | awk '{print $1}'
  else
    demo_fail "No SHA-256 utility was found."
  fi
}

demo_selected_sdk() {
  local package_dir="$1"
  (
    cd "$package_dir"
    "$DEMO_DPM_BIN" version
  ) 2>/dev/null | awk '/^[[:space:]]*\*/ {print $2; exit}'
}

demo_canton_version() {
  local package_dir="$1"
  (
    cd "$package_dir"
    "$DEMO_DPM_BIN" sandbox --help
  ) 2>&1 | awk 'NR == 1 {sub(/^Canton v/, ""); print; exit}'
}

demo_package_needs_build() {
  local package_dir="$1"
  local dar_file="$2"
  local source_file=""

  if [[ ! -f "$dar_file" || "$package_dir/daml.yaml" -nt "$dar_file" ]]; then
    return 0
  fi
  while IFS= read -r source_file; do
    if [[ "$source_file" -nt "$dar_file" ]]; then
      return 0
    fi
  done < <(find "$package_dir/daml" -type f -name '*.daml' -print)
  return 1
}

demo_build_package() {
  local label="$1"
  local package_dir="$2"
  local dar_file="$3"
  local log_file="$4"

  if demo_package_needs_build "$package_dir" "$dar_file"; then
    if ! (
      cd "$package_dir"
      "$DEMO_DPM_BIN" build
    ) >"$log_file" 2>&1; then
      printf '\n%s build failed. Last log lines:\n' "$label" >&2
      tail -n 40 "$log_file" >&2 || true
      demo_fail "$label Daml build failed. Full log: $log_file"
      return 1
    fi
    DEMO_LAST_BUILD_STATE="built"
  else
    DEMO_LAST_BUILD_STATE="current"
  fi

  if [[ ! -s "$dar_file" ]]; then
    demo_fail "$label DAR is missing after build: $dar_file"
    return 1
  fi
}

demo_ledger_ready() {
  [[ -s "$DEMO_CANTON_PORT_FILE" ]] &&
  "$DEMO_CURL_BIN" --fail --silent --show-error \
    --connect-timeout 1 --max-time 3 \
    "$DEMO_LEDGER_URL/livez" >/dev/null 2>&1 &&
  "$DEMO_CURL_BIN" --fail --silent --show-error \
    --connect-timeout 1 --max-time 3 \
    "$DEMO_LEDGER_URL/v2/state/ledger-end" >/dev/null 2>&1
}

demo_port_has_listener() {
  local port_number="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port_number" -sTCP:LISTEN >/dev/null 2>&1
  elif command -v nc >/dev/null 2>&1; then
    nc -z "$DEMO_LEDGER_HOST" "$port_number" >/dev/null 2>&1
  else
    demo_fail "Neither lsof nor nc is available for the required port safety check."
  fi
}

demo_require_port_probe() {
  if ! command -v lsof >/dev/null 2>&1 && ! command -v nc >/dev/null 2>&1; then
    demo_fail "Neither lsof nor nc is available for the required port safety check."
    return 1
  fi
}

demo_owned_ports_bound() {
  local owned_port=""
  local owned_ports=(
    "$DEMO_LEDGER_GRPC_PORT"
    "$DEMO_LEDGER_ADMIN_PORT"
    "$DEMO_SEQUENCER_PUBLIC_PORT"
    "$DEMO_SEQUENCER_ADMIN_PORT"
    "$DEMO_MEDIATOR_ADMIN_PORT"
    "$DEMO_LEDGER_HTTP_PORT"
  )
  for owned_port in "${owned_ports[@]}"; do
    if demo_port_has_listener "$owned_port"; then
      return 0
    fi
  done
  return 1
}

demo_process_group_alive() {
  local process_group="$1"
  if [[ ! "$process_group" =~ ^[0-9]+$ ]]; then
    return 1
  fi
  ps -axo pgid=,stat= | awk -v group="$process_group" '
    $1 == group && $2 !~ /^Z/ { found = 1 }
    END { exit(found ? 0 : 1) }
  '
}

demo_wait_for_group_marker() {
  local attempt="0"
  local marker_pid=""
  while (( attempt < 100 )); do
    marker_pid="$(sed -n '1p' "$DEMO_CANTON_GROUP_FILE" 2>/dev/null || true)"
    if [[ "$marker_pid" == "$DEMO_CANTON_PID" ]]; then
      DEMO_CANTON_PGID="$marker_pid"
      DEMO_CANTON_GROUP_READY="1"
      return 0
    fi
    if ! kill -0 "$DEMO_CANTON_PID" 2>/dev/null; then
      return 1
    fi
    sleep 0.05
    attempt=$((attempt + 1))
  done
  return 1
}

demo_wait_for_ledger() {
  local attempt="0"
  while (( attempt < 120 )); do
    if demo_ledger_ready; then
      return 0
    fi
    if [[ -n "$DEMO_CANTON_PID" ]] && ! kill -0 "$DEMO_CANTON_PID" 2>/dev/null; then
      return 1
    fi
    sleep 0.5
    attempt=$((attempt + 1))
  done
  return 1
}

demo_start_fresh_canton() {
  local occupied_port=""
  local marker_pgid=""
  local actual_pgid=""
  local required_ports=(
    "$DEMO_LEDGER_GRPC_PORT"
    "$DEMO_LEDGER_ADMIN_PORT"
    "$DEMO_SEQUENCER_PUBLIC_PORT"
    "$DEMO_SEQUENCER_ADMIN_PORT"
    "$DEMO_MEDIATOR_ADMIN_PORT"
    "$DEMO_LEDGER_HTTP_PORT"
  )

  mkdir -p "$DEMO_RUN_DIR"
  if ! demo_require_port_probe; then
    return 1
  fi
  for occupied_port in "${required_ports[@]}"; do
    if demo_port_has_listener "$occupied_port"; then
      demo_fail "Fresh Canton needs port $occupied_port, but it is already occupied. No process was stopped."
      return 1
    fi
  done

  rm -f \
    "$DEMO_CANTON_PID_FILE" \
    "$DEMO_CANTON_GROUP_FILE" \
    "$DEMO_CANTON_PORT_FILE"
  : >"$DEMO_RUN_DIR/canton.stdout.log"
  (
    cd "$DEMO_COLLATERAL_DIR"
    exec "$DEMO_SYSTEM_PYTHON" -c '
import os
import sys

marker_file = sys.argv[1]
executable = sys.argv[2]
arguments = sys.argv[2:]

os.setsid()
with open(marker_file, "w", encoding="utf-8") as marker:
    marker.write(f"{os.getpid()}\n")
    marker.flush()
    os.fsync(marker.fileno())
os.execv(executable, arguments)
' "$DEMO_CANTON_GROUP_FILE" "$DEMO_DPM_BIN" sandbox \
      --ledger-api-port "$DEMO_LEDGER_GRPC_PORT" \
      --admin-api-port "$DEMO_LEDGER_ADMIN_PORT" \
      --sequencer-public-port "$DEMO_SEQUENCER_PUBLIC_PORT" \
      --sequencer-admin-port "$DEMO_SEQUENCER_ADMIN_PORT" \
      --mediator-admin-port "$DEMO_MEDIATOR_ADMIN_PORT" \
      --json-api-port "$DEMO_LEDGER_HTTP_PORT" \
      --canton-port-file "$DEMO_CANTON_PORT_FILE" \
      --log-file-name "$DEMO_RUN_DIR/canton.log" \
      --log-file-appender flat \
      --log-truncate
  ) >>"$DEMO_RUN_DIR/canton.stdout.log" 2>&1 &
  DEMO_CANTON_PID="$!"
  DEMO_CANTON_PGID="$DEMO_CANTON_PID"
  DEMO_CANTON_STARTED="1"
  printf '%s\n' "$DEMO_CANTON_PID" >"$DEMO_CANTON_PID_FILE"

  if ! demo_wait_for_group_marker; then
    printf '\nCanton process-group wrapper failed. Last log lines:\n' >&2
    tail -n 60 "$DEMO_RUN_DIR/canton.stdout.log" >&2 || true
    demo_cleanup_canton
    demo_fail "Canton could not enter its owned process group."
    return 1
  fi

  marker_pgid="$(sed -n '1p' "$DEMO_CANTON_GROUP_FILE" 2>/dev/null || true)"
  actual_pgid="$(ps -p "$DEMO_CANTON_PID" -o pgid= 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ "$marker_pgid" != "$DEMO_CANTON_PID" || "$actual_pgid" != "$DEMO_CANTON_PID" ]]; then
    demo_cleanup_canton
    demo_fail "Canton process-group ownership could not be verified."
    return 1
  fi

  if ! demo_wait_for_ledger; then
    printf '\nCanton did not become ready. Last log lines:\n' >&2
    tail -n 60 "$DEMO_RUN_DIR/canton.stdout.log" >&2 || true
    demo_cleanup_canton
    demo_fail "Canton startup failed. Full log: $DEMO_RUN_DIR/canton.stdout.log"
    return 1
  fi
}

demo_upload_dar() {
  local label="$1"
  local dar_file="$2"
  local upload_log="$DEMO_RUN_DIR/${label}.upload.log"
  local attempt="0"
  local curl_status="0"
  local http_status=""

  if [[ ! -s "$dar_file" ]]; then
    demo_fail "Cannot load missing $label DAR: $dar_file"
    return 1
  fi

  # The HTTP endpoint can answer before the in-memory sandbox has connected
  # its synchronizer. Retry only that explicit Canton startup race; all other
  # upload failures remain immediate and visible.
  while (( attempt < 120 )); do
    curl_status="0"
    if http_status="$("$DEMO_CURL_BIN" --silent --show-error \
      --max-time 120 \
      --request POST \
      --header 'Content-Type: application/octet-stream' \
      --data-binary "@$dar_file" \
      --output "$upload_log" \
      --write-out '%{http_code}' \
      "$DEMO_LEDGER_URL/v2/packages")"; then
      curl_status="0"
    else
      curl_status="$?"
    fi

    if [[ "$curl_status" == "0" && "$http_status" == 2* ]]; then
      return 0
    fi
    if [[ "$http_status" == "400" ]] &&
       grep -F 'PACKAGE_SERVICE_CANNOT_AUTODETECT_SYNCHRONIZER' "$upload_log" >/dev/null 2>&1; then
      sleep 0.5
      attempt=$((attempt + 1))
      continue
    fi

    cat "$upload_log" >&2 || true
    demo_fail "Canton rejected the $label DAR (HTTP $http_status, curl $curl_status). See $upload_log"
    return 1
  done

  cat "$upload_log" >&2 || true
  demo_fail "Canton synchronizer was not ready to load the $label DAR. See $upload_log"
}

demo_stop_exact_pid() {
  local owned_pid="$1"
  local attempt="0"
  if [[ -z "$owned_pid" ]] || ! kill -0 "$owned_pid" 2>/dev/null; then
    return 0
  fi
  kill -TERM "$owned_pid" 2>/dev/null || true
  while (( attempt < 40 )); do
    if ! kill -0 "$owned_pid" 2>/dev/null; then
      return 0
    fi
    sleep 0.25
    attempt=$((attempt + 1))
  done
  kill -KILL "$owned_pid" 2>/dev/null || true
}

demo_cleanup_canton() {
  local attempt="0"
  local cleanup_failed="0"
  local current_pgid=""
  local recorded_pid=""
  local marker_pgid=""

  if [[ "$DEMO_CANTON_STARTED" != "1" ]]; then
    return 0
  fi

  recorded_pid="$(sed -n '1p' "$DEMO_CANTON_PID_FILE" 2>/dev/null || true)"
  marker_pgid="$(sed -n '1p' "$DEMO_CANTON_GROUP_FILE" 2>/dev/null || true)"
  current_pgid="$(ps -p "$$" -o pgid= 2>/dev/null | tr -d '[:space:]' || true)"

  if [[ "$DEMO_CANTON_GROUP_READY" == "1" &&
        "$recorded_pid" == "$DEMO_CANTON_PID" &&
        "$marker_pgid" == "$DEMO_CANTON_PGID" &&
        "$DEMO_CANTON_PGID" =~ ^[0-9]+$ &&
        "$DEMO_CANTON_PGID" != "$current_pgid" ]]; then
    kill -TERM -- "-$DEMO_CANTON_PGID" 2>/dev/null || true
    while (( attempt < 40 )); do
      if ! demo_process_group_alive "$DEMO_CANTON_PGID"; then
        break
      fi
      sleep 0.25
      attempt=$((attempt + 1))
    done
    if demo_process_group_alive "$DEMO_CANTON_PGID"; then
      kill -KILL -- "-$DEMO_CANTON_PGID" 2>/dev/null || true
    fi
  else
    # Before the setsid marker exists, the wrapper has not spawned Java. Only
    # that exact wrapper PID is safe to stop.
    demo_stop_exact_pid "$DEMO_CANTON_PID"
  fi

  if [[ -n "$DEMO_CANTON_PID" ]]; then
    wait "$DEMO_CANTON_PID" 2>/dev/null || true
  fi

  # Do not return control to an immediate second run until every descendant is
  # gone and every port owned by this sandbox is unbound.
  attempt="0"
  while (( attempt < 40 )); do
    if ! demo_process_group_alive "$DEMO_CANTON_PGID" && ! demo_owned_ports_bound; then
      break
    fi
    sleep 0.25
    attempt=$((attempt + 1))
  done
  if demo_process_group_alive "$DEMO_CANTON_PGID"; then
    demo_fail "Owned Canton process group $DEMO_CANTON_PGID did not exit."
    cleanup_failed="1"
  fi
  if demo_owned_ports_bound; then
    demo_fail "One or more owned Canton ports remained bound after cleanup."
    cleanup_failed="1"
  fi

  if [[ "$cleanup_failed" == "0" ]]; then
    if [[ "$(sed -n '1p' "$DEMO_CANTON_PID_FILE" 2>/dev/null || true)" == "$DEMO_CANTON_PID" ]]; then
      rm -f "$DEMO_CANTON_PID_FILE"
    fi
    if [[ "$(sed -n '1p' "$DEMO_CANTON_GROUP_FILE" 2>/dev/null || true)" == "$DEMO_CANTON_PGID" ]]; then
      rm -f "$DEMO_CANTON_GROUP_FILE"
    fi
  fi
  DEMO_CANTON_STARTED="0"
  DEMO_CANTON_GROUP_READY="0"
  return "$cleanup_failed"
}

demo_acquire_lock() {
  local lock_dir="$DEMO_RUN_DIR/demo.lock"
  local owner_pid=""
  mkdir -p "$DEMO_RUN_DIR"

  if mkdir "$lock_dir" 2>/dev/null; then
    printf '%s\n' "$$" >"$lock_dir/pid"
    DEMO_LOCK_HELD="1"
    return 0
  fi
  owner_pid="$(sed -n '1p' "$lock_dir/pid" 2>/dev/null || true)"
  if [[ "$owner_pid" =~ ^[0-9]+$ ]] && kill -0 "$owner_pid" 2>/dev/null; then
    demo_fail "Another ./demo.sh process is already running (PID $owner_pid)."
    return 1
  fi

  rm -f "$lock_dir/pid"
  rmdir "$lock_dir" 2>/dev/null || true
  if ! mkdir "$lock_dir" 2>/dev/null; then
    demo_fail "Could not acquire the demo runtime lock: $lock_dir"
    return 1
  fi
  printf '%s\n' "$$" >"$lock_dir/pid"
  DEMO_LOCK_HELD="1"
}

demo_release_lock() {
  local lock_dir="$DEMO_RUN_DIR/demo.lock"
  if [[ "$DEMO_LOCK_HELD" == "1" ]]; then
    rm -f "$lock_dir/pid"
    rmdir "$lock_dir" 2>/dev/null || true
    DEMO_LOCK_HELD="0"
  fi
}
