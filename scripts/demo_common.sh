#!/usr/bin/env bash

# Shared, source-only helpers for the root demo commands. Keep every path
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

DEMO_LEDGER_HOST="127.0.0.1"
DEMO_LEDGER_HTTP_PORT="7575"
DEMO_LEDGER_GRPC_PORT="6865"
DEMO_LEDGER_URL="http://$DEMO_LEDGER_HOST:$DEMO_LEDGER_HTTP_PORT"

# The optimizer-to-ledger bridge deliberately requires a fresh ledger. It gets
# a private, non-conflicting Canton topology so an existing wallet sandbox on
# 7575 can be reused without being killed or polluted.
DEMO_COLLATERAL_HTTP_PORT="7675"
DEMO_COLLATERAL_GRPC_PORT="7865"
DEMO_COLLATERAL_ADMIN_PORT="7866"
DEMO_COLLATERAL_SEQUENCER_PORT="7867"
DEMO_COLLATERAL_SEQUENCER_ADMIN_PORT="7868"
DEMO_COLLATERAL_MEDIATOR_ADMIN_PORT="7869"
DEMO_COLLATERAL_LEDGER_URL="http://$DEMO_LEDGER_HOST:$DEMO_COLLATERAL_HTTP_PORT"

DEMO_DPM_BIN="${DEMO_DPM_BIN:-}"
DEMO_SYSTEM_PYTHON=""
DEMO_CURL_BIN=""
DEMO_JAVA_HOME=""
DEMO_CANTON_PID=""
DEMO_CANTON_CHILD_PID=""
DEMO_CANTON_STARTED="0"
DEMO_CANTON_MODE=""
DEMO_COLLATERAL_CANTON_PID=""
DEMO_COLLATERAL_CANTON_CHILD_PID=""
DEMO_COLLATERAL_CANTON_STARTED="0"
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
  demo_url_ready "$DEMO_LEDGER_URL"
}

demo_url_ready() {
  local ledger_url="$1"
  "$DEMO_CURL_BIN" --fail --silent --show-error \
    --connect-timeout 1 --max-time 3 \
    "$ledger_url/livez" >/dev/null 2>&1 &&
  "$DEMO_CURL_BIN" --fail --silent --show-error \
    --connect-timeout 1 --max-time 3 \
    "$ledger_url/v2/state/ledger-end" >/dev/null 2>&1
}

demo_port_has_listener() {
  local port_number="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$port_number" -sTCP:LISTEN >/dev/null 2>&1
  elif command -v nc >/dev/null 2>&1; then
    nc -z "$DEMO_LEDGER_HOST" "$port_number" >/dev/null 2>&1
  else
    return 1
  fi
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

demo_wait_for_collateral_ledger() {
  local attempt="0"
  while (( attempt < 120 )); do
    if demo_url_ready "$DEMO_COLLATERAL_LEDGER_URL"; then
      return 0
    fi
    if [[ -n "$DEMO_COLLATERAL_CANTON_PID" ]] &&
       ! kill -0 "$DEMO_COLLATERAL_CANTON_PID" 2>/dev/null; then
      return 1
    fi
    sleep 0.5
    attempt=$((attempt + 1))
  done
  return 1
}

demo_capture_canton_child() {
  if [[ -n "$DEMO_CANTON_PID" ]] && command -v pgrep >/dev/null 2>&1; then
    DEMO_CANTON_CHILD_PID="$(pgrep -P "$DEMO_CANTON_PID" | head -n 1 || true)"
  fi
}

demo_start_or_reuse_canton() {
  local occupied_port=""
  mkdir -p "$DEMO_RUN_DIR"

  if demo_ledger_ready; then
    DEMO_CANTON_MODE="reused"
    return 0
  fi

  for occupied_port in 6865 6866 6867 6868 6869 7575; do
    if demo_port_has_listener "$occupied_port"; then
      demo_fail "Port $occupied_port is occupied, but Canton at $DEMO_LEDGER_URL is not healthy. No process was stopped."
      return 1
    fi
  done

  : >"$DEMO_RUN_DIR/canton.stdout.log"
  (
    cd "$DEMO_WALLET_DIR"
    exec "$DEMO_DPM_BIN" sandbox \
      --ledger-api-port "$DEMO_LEDGER_GRPC_PORT" \
      --json-api-port "$DEMO_LEDGER_HTTP_PORT" \
      --canton-port-file "$DEMO_RUN_DIR/canton-ports.json" \
      --log-file-name "$DEMO_RUN_DIR/canton.log" \
      --log-file-appender flat \
      --log-truncate \
      --dar "$DEMO_WALLET_DAR"
  ) >>"$DEMO_RUN_DIR/canton.stdout.log" 2>&1 &
  DEMO_CANTON_PID="$!"
  DEMO_CANTON_STARTED="1"
  DEMO_CANTON_MODE="started"
  printf '%s\n' "$DEMO_CANTON_PID" >"$DEMO_RUN_DIR/canton.pid"

  if ! demo_wait_for_ledger; then
    printf '\nCanton did not become ready. Last log lines:\n' >&2
    tail -n 60 "$DEMO_RUN_DIR/canton.stdout.log" >&2 || true
    demo_cleanup_canton
    demo_fail "Canton startup failed. Full log: $DEMO_RUN_DIR/canton.stdout.log"
    return 1
  fi
  demo_capture_canton_child
}

demo_start_fresh_collateral_canton() {
  local occupied_port=""
  local collateral_ports=""
  mkdir -p "$DEMO_RUN_DIR"

  collateral_ports="${DEMO_COLLATERAL_GRPC_PORT} ${DEMO_COLLATERAL_ADMIN_PORT} ${DEMO_COLLATERAL_SEQUENCER_PORT} ${DEMO_COLLATERAL_SEQUENCER_ADMIN_PORT} ${DEMO_COLLATERAL_MEDIATOR_ADMIN_PORT} ${DEMO_COLLATERAL_HTTP_PORT}"
  for occupied_port in $collateral_ports; do
    if demo_port_has_listener "$occupied_port"; then
      demo_fail "Fresh collateral Canton needs port $occupied_port, but it is already occupied. No process was stopped."
      return 1
    fi
  done

  : >"$DEMO_RUN_DIR/collateral-canton.stdout.log"
  (
    cd "$DEMO_COLLATERAL_DIR"
    exec "$DEMO_DPM_BIN" sandbox \
      --ledger-api-port "$DEMO_COLLATERAL_GRPC_PORT" \
      --admin-api-port "$DEMO_COLLATERAL_ADMIN_PORT" \
      --sequencer-public-port "$DEMO_COLLATERAL_SEQUENCER_PORT" \
      --sequencer-admin-port "$DEMO_COLLATERAL_SEQUENCER_ADMIN_PORT" \
      --mediator-admin-port "$DEMO_COLLATERAL_MEDIATOR_ADMIN_PORT" \
      --json-api-port "$DEMO_COLLATERAL_HTTP_PORT" \
      --canton-port-file "$DEMO_RUN_DIR/collateral-canton-ports.json" \
      --log-file-name "$DEMO_RUN_DIR/collateral-canton.log" \
      --log-file-appender flat \
      --log-truncate \
      --dar "$DEMO_COLLATERAL_DAR"
  ) >>"$DEMO_RUN_DIR/collateral-canton.stdout.log" 2>&1 &
  DEMO_COLLATERAL_CANTON_PID="$!"
  DEMO_COLLATERAL_CANTON_STARTED="1"
  printf '%s\n' "$DEMO_COLLATERAL_CANTON_PID" >"$DEMO_RUN_DIR/collateral-canton.pid"

  if ! demo_wait_for_collateral_ledger; then
    printf '\nFresh collateral Canton did not become ready. Last log lines:\n' >&2
    tail -n 60 "$DEMO_RUN_DIR/collateral-canton.stdout.log" >&2 || true
    demo_cleanup_collateral_canton
    demo_fail "Collateral Canton startup failed. Full log: $DEMO_RUN_DIR/collateral-canton.stdout.log"
    return 1
  fi
  if command -v pgrep >/dev/null 2>&1; then
    DEMO_COLLATERAL_CANTON_CHILD_PID="$(pgrep -P "$DEMO_COLLATERAL_CANTON_PID" | head -n 1 || true)"
  fi
}

demo_upload_dar() {
  local label="$1"
  local dar_file="$2"
  local upload_log="$DEMO_RUN_DIR/${label}.upload.log"

  if [[ ! -s "$dar_file" ]]; then
    demo_fail "Cannot load missing $label DAR: $dar_file"
    return 1
  fi
  if ! "$DEMO_CURL_BIN" --fail --silent --show-error \
    --max-time 120 \
    --request POST \
    --header 'Content-Type: application/octet-stream' \
    --data-binary "@$dar_file" \
    "$DEMO_LEDGER_URL/v2/packages" >"$upload_log" 2>&1; then
    cat "$upload_log" >&2 || true
    demo_fail "Canton rejected the $label DAR. See $upload_log"
    return 1
  fi
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
  local child_command=""
  if [[ "$DEMO_CANTON_STARTED" != "1" ]]; then
    return 0
  fi

  demo_stop_exact_pid "$DEMO_CANTON_PID"
  if [[ -n "$DEMO_CANTON_PID" ]]; then
    wait "$DEMO_CANTON_PID" 2>/dev/null || true
  fi

  # DPM normally forwards TERM to its Java child. If it did not, stop only the
  # exact child captured from the DPM process, after confirming it is Canton.
  if [[ -n "$DEMO_CANTON_CHILD_PID" ]] && kill -0 "$DEMO_CANTON_CHILD_PID" 2>/dev/null; then
    child_command="$(ps -p "$DEMO_CANTON_CHILD_PID" -o command= 2>/dev/null || true)"
    if [[ "$child_command" == *canton* ]]; then
      demo_stop_exact_pid "$DEMO_CANTON_CHILD_PID"
    fi
  fi

  if [[ -f "$DEMO_RUN_DIR/canton.pid" ]] &&
     [[ "$(sed -n '1p' "$DEMO_RUN_DIR/canton.pid")" == "$DEMO_CANTON_PID" ]]; then
    rm -f "$DEMO_RUN_DIR/canton.pid"
  fi
  DEMO_CANTON_STARTED="0"
}

demo_cleanup_collateral_canton() {
  local child_command=""
  if [[ "$DEMO_COLLATERAL_CANTON_STARTED" != "1" ]]; then
    return 0
  fi

  demo_stop_exact_pid "$DEMO_COLLATERAL_CANTON_PID"
  if [[ -n "$DEMO_COLLATERAL_CANTON_PID" ]]; then
    wait "$DEMO_COLLATERAL_CANTON_PID" 2>/dev/null || true
  fi
  if [[ -n "$DEMO_COLLATERAL_CANTON_CHILD_PID" ]] &&
     kill -0 "$DEMO_COLLATERAL_CANTON_CHILD_PID" 2>/dev/null; then
    child_command="$(ps -p "$DEMO_COLLATERAL_CANTON_CHILD_PID" -o command= 2>/dev/null || true)"
    if [[ "$child_command" == *canton* ]]; then
      demo_stop_exact_pid "$DEMO_COLLATERAL_CANTON_CHILD_PID"
    fi
  fi

  if [[ -f "$DEMO_RUN_DIR/collateral-canton.pid" ]] &&
     [[ "$(sed -n '1p' "$DEMO_RUN_DIR/collateral-canton.pid")" == "$DEMO_COLLATERAL_CANTON_PID" ]]; then
    rm -f "$DEMO_RUN_DIR/collateral-canton.pid"
  fi
  DEMO_COLLATERAL_CANTON_STARTED="0"
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
