#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source "$ROOT_DIR/scripts/demo_common.sh"

cd "$DEMO_ROOT"
mkdir -p "$DEMO_RUN_DIR"

if ! "$DEMO_ROOT/setup_demo.sh" --quiet; then
  demo_fail "Environment setup failed. Run ./setup_demo.sh for details."
  exit 1
fi
demo_configure_toolchain

printf '==================================================\n'
printf 'CANTON COLLATERAL OPTIMIZER TESTS\n'
printf '==================================================\n\n'

printf '[1/5] Launcher syntax\n'
bash -n \
  "$DEMO_ROOT/setup_demo.sh" \
  "$DEMO_ROOT/demo.sh" \
  "$DEMO_ROOT/test_all.sh" \
  "$DEMO_ROOT/scripts/demo_common.sh"
printf '✓ Shell syntax\n\n'

printf '[2/5] Optimizer tests\n'
if ! "$DEMO_PYTHON" -B -m pytest -q tests/test_optimizer.py \
  >"$DEMO_RUN_DIR/optimizer-tests.log" 2>&1; then
  cat "$DEMO_RUN_DIR/optimizer-tests.log" >&2
  exit 1
fi
cat "$DEMO_RUN_DIR/optimizer-tests.log"
printf '\n'

printf '[3/5] Python test suite\n'
if ! "$DEMO_PYTHON" -B -m pytest -q tests \
  >"$DEMO_RUN_DIR/python-tests.log" 2>&1; then
  cat "$DEMO_RUN_DIR/python-tests.log" >&2
  exit 1
fi
cat "$DEMO_RUN_DIR/python-tests.log"
printf '\n'

printf '[4/5] Daml contract tests\n'
if ! (
  cd "$DEMO_COLLATERAL_DIR"
  "$DEMO_DPM_BIN" test
) >"$DEMO_RUN_DIR/collateral-tests.log" 2>&1; then
  tail -n 100 "$DEMO_RUN_DIR/collateral-tests.log" >&2 || true
  demo_fail "Collateral Daml tests failed. Full log: $DEMO_RUN_DIR/collateral-tests.log"
  exit 1
fi
printf '✓ Collateral Daml tests\n'

if ! (
  cd "$DEMO_WALLET_DIR"
  "$DEMO_DPM_BIN" test
) >"$DEMO_RUN_DIR/wallet-tests.log" 2>&1; then
  tail -n 100 "$DEMO_RUN_DIR/wallet-tests.log" >&2 || true
  demo_fail "Wallet Daml tests failed. Full log: $DEMO_RUN_DIR/wallet-tests.log"
  exit 1
fi
printf '✓ Wallet Daml tests\n\n'

printf '[5/5] Diff integrity\n'
git diff --check
git diff --cached --check
printf '✓ git diff --check\n\n'

printf 'TEST ALL PASS\n'
