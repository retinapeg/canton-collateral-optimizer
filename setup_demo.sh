#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source "$ROOT_DIR/scripts/demo_common.sh"

SETUP_QUIET="0"
if [[ "${1:-}" == "--quiet" ]]; then
  SETUP_QUIET="1"
elif [[ -n "${1:-}" ]]; then
  demo_fail "Unknown setup option: $1"
  exit 2
fi

setup_line() {
  if [[ "$SETUP_QUIET" != "1" ]]; then
    printf '%s\n' "$*"
  fi
}

mkdir -p "$DEMO_RUN_DIR"
cd "$DEMO_ROOT"

setup_line "=================================================="
setup_line "CANTON COLLATERAL OPTIMIZER SETUP"
setup_line "=================================================="
setup_line ""
setup_line "[1/3] Toolchain"

demo_find_system_python
demo_configure_toolchain

if [[ ! -x "$DEMO_PYTHON" ]]; then
  setup_line "Creating persistent Python environment at .venv ..."
  "$DEMO_SYSTEM_PYTHON" -m venv "$DEMO_VENV_DIR"
fi
if [[ ! -x "$DEMO_PYTHON" ]]; then
  demo_fail "The repository-local Python environment could not be created."
  exit 1
fi
if ! "$DEMO_PYTHON" -m pip --version >/dev/null 2>&1; then
  demo_fail "pip is missing from $DEMO_VENV_DIR. Recreate that environment and rerun setup."
  exit 1
fi

COLLATERAL_SDK="$(demo_selected_sdk "$DEMO_COLLATERAL_DIR")"
WALLET_SDK="$(demo_selected_sdk "$DEMO_WALLET_DIR")"
CANTON_VERSION="$(demo_canton_version "$DEMO_WALLET_DIR")"
if [[ -z "$COLLATERAL_SDK" || -z "$WALLET_SDK" || -z "$CANTON_VERSION" ]]; then
  demo_fail "DPM is present, but its package SDK/Canton components could not be resolved."
  exit 1
fi

setup_line "✓ Python $($DEMO_PYTHON --version 2>&1 | awk '{print $2}')"
setup_line "✓ DPM (collateral SDK $COLLATERAL_SDK; wallet SDK $WALLET_SDK)"
setup_line "✓ Canton $CANTON_VERSION"

setup_line ""
setup_line "[2/3] Python dependencies"
if [[ ! -f "$DEMO_REQUIREMENTS" ]]; then
  demo_fail "Dependency manifest is missing: $DEMO_REQUIREMENTS"
  exit 1
fi

REQUIREMENTS_HASH="$(demo_sha256 "$DEMO_REQUIREMENTS")"
INSTALLED_HASH="$(sed -n '1p' "$DEMO_REQUIREMENTS_MARKER" 2>/dev/null || true)"
INSTALL_NEEDED="0"
if [[ "$REQUIREMENTS_HASH" != "$INSTALLED_HASH" ]]; then
  INSTALL_NEEDED="1"
fi
if ! "$DEMO_PYTHON" -c 'import numpy, scipy, pytest' >/dev/null 2>&1; then
  INSTALL_NEEDED="1"
fi
if ! "$DEMO_PYTHON" -m pip check >/dev/null 2>&1; then
  INSTALL_NEEDED="1"
fi

if [[ "$INSTALL_NEEDED" == "1" ]]; then
  setup_line "Installing only the missing/changed demo dependencies ..."
  if ! "$DEMO_PYTHON" -m pip install \
    --disable-pip-version-check \
    --requirement "$DEMO_REQUIREMENTS" \
    >"$DEMO_RUN_DIR/pip-install.log" 2>&1; then
    tail -n 60 "$DEMO_RUN_DIR/pip-install.log" >&2 || true
    demo_fail "Python dependency installation failed. Full log: $DEMO_RUN_DIR/pip-install.log"
    exit 1
  fi
  printf '%s\n' "$REQUIREMENTS_HASH" >"$DEMO_REQUIREMENTS_MARKER"
  setup_line "✓ Dependencies installed"
else
  setup_line "✓ Dependencies already current; pip was not run"
fi

if ! "$DEMO_PYTHON" -c 'import numpy, scipy, pytest' >/dev/null 2>&1; then
  demo_fail "Required Python imports still fail after setup. See $DEMO_RUN_DIR/pip-install.log"
  exit 1
fi
if ! "$DEMO_PYTHON" -m pip check >"$DEMO_RUN_DIR/pip-check.log" 2>&1; then
  cat "$DEMO_RUN_DIR/pip-check.log" >&2
  demo_fail "The Python environment has inconsistent dependencies."
  exit 1
fi

setup_line ""
setup_line "[3/3] Daml packages"
demo_build_package \
  "Collateral" \
  "$DEMO_COLLATERAL_DIR" \
  "$DEMO_COLLATERAL_DAR" \
  "$DEMO_RUN_DIR/collateral-build.log"
setup_line "✓ Collateral package $DEMO_LAST_BUILD_STATE"

demo_build_package \
  "Wallet" \
  "$DEMO_WALLET_DIR" \
  "$DEMO_WALLET_DAR" \
  "$DEMO_RUN_DIR/wallet-build.log"
setup_line "✓ Wallet package $DEMO_LAST_BUILD_STATE"

setup_line ""
setup_line "SETUP PASS"
