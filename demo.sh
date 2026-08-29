#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source "$ROOT_DIR/scripts/demo_common.sh"

demo_on_exit() {
  local exit_status="$?"
  trap - EXIT
  demo_cleanup_canton || true
  demo_release_lock || true
  if [[ "$exit_status" -ne 0 ]]; then
    printf '\nDEMO FAIL\n'
    printf 'Logs: %s\n' "$DEMO_RUN_DIR"
  fi
  exit "$exit_status"
}

trap demo_on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

cd "$DEMO_ROOT"
mkdir -p "$DEMO_RUN_DIR"
demo_acquire_lock

if ! "$DEMO_ROOT/setup_demo.sh" --quiet; then
  demo_fail "Environment setup failed. Run ./setup_demo.sh for the detailed check."
  exit 1
fi
demo_configure_toolchain

printf '==================================================\n'
printf 'CANTON COLLATERAL OPTIMIZER DEMO\n'
printf '==================================================\n\n'

printf '[1/5] Environment\n'
printf '✓ Python %s (.venv)\n' "$($DEMO_PYTHON --version 2>&1 | awk '{print $2}')"
printf '✓ DPM (collateral SDK %s; wallet SDK %s)\n' \
  "$(demo_selected_sdk "$DEMO_COLLATERAL_DIR")" \
  "$(demo_selected_sdk "$DEMO_WALLET_DIR")"
printf '✓ Canton %s available\n\n' "$(demo_canton_version "$DEMO_WALLET_DIR")"

printf '[2/5] Global optimisation\n\n'
if ! "$DEMO_PYTHON" -B -m optimizer >"$DEMO_RUN_DIR/optimizer.log" 2>&1; then
  cat "$DEMO_RUN_DIR/optimizer.log" >&2
  demo_fail "The global optimizer command failed."
  exit 1
fi
if ! grep -Fx 'OPTIMIZER PASS' "$DEMO_RUN_DIR/optimizer.log" >/dev/null; then
  cat "$DEMO_RUN_DIR/optimizer.log" >&2
  demo_fail "The optimizer did not emit its required OPTIMIZER PASS marker."
  exit 1
fi
awk '
  /Greedy\/local example cost:/ ||
  /Global allocation:/ ||
  /^Asset[0-9]+ -> Institution/ ||
  /Global optimum cost:/ ||
  /Savings vs local allocation:/ ||
  /^OPTIMIZER PASS$/ { print }
' "$DEMO_RUN_DIR/optimizer.log"
printf '\n'

printf '[3/5] Canton / Daml contracts\n'
demo_start_or_reuse_canton
if [[ "$DEMO_CANTON_MODE" == "reused" ]]; then
  printf '✓ Reusing healthy Canton at %s\n' "$DEMO_LEDGER_URL"
else
  printf '✓ Canton started and ready at %s\n' "$DEMO_LEDGER_URL"
fi
demo_upload_dar "collateral" "$DEMO_COLLATERAL_DAR"
demo_upload_dar "wallet" "$DEMO_WALLET_DAR"
printf '✓ Daml builds current\n'
printf '✓ Collateral and wallet packages loaded\n\n'

printf '[4/5] Institutional allocation\n\n'
if ! "$DEMO_PYTHON" -B -m backend.allocation_demo \
  --base-url "$DEMO_LEDGER_URL" \
  2>&1 | tee "$DEMO_RUN_DIR/allocation-demo.log"; then
  demo_fail "The optimizer-to-ledger institutional allocation failed."
  exit 1
fi
printf '\n✓ Institutional allocation workflow verified on Canton\n\n'

printf '[5/5] Spend-limited wallet\n\n'
if ! env \
  -u C8_BASE \
  -u C8_IDP \
  -u C8_CLIENT_SECRET \
  AGENT_WALLET_NETWORK=sandbox \
  C8_USER=agent-wallet \
  PYTHONUNBUFFERED=1 \
  "$DEMO_PYTHON" -B -m agent_wallet.demo \
  --base-url "$DEMO_LEDGER_URL" \
  --out "$DEMO_WALLET_DIR/out" \
  >"$DEMO_RUN_DIR/wallet-demo.log" 2>&1; then
  tail -n 100 "$DEMO_RUN_DIR/wallet-demo.log" >&2 || true
  demo_fail "The spend-limited wallet demo failed."
  exit 1
fi

for required_evidence in \
  'the payees actually received money, not just a counter increment' \
  'the agent never spent more than its cap' \
  'pay someone not on the allow-list' \
  'raid the account directly' \
  'spend after revocation' \
  'the receipts account for every penny that moved' \
  'All limits held on the ledger. Revocation was immediate.'
do
  if ! grep -F "$required_evidence" "$DEMO_RUN_DIR/wallet-demo.log" >/dev/null; then
    tail -n 100 "$DEMO_RUN_DIR/wallet-demo.log" >&2 || true
    demo_fail "Wallet evidence is incomplete: $required_evidence"
    exit 1
  fi
done

printf '✓ real ledger balances moved\n'
printf '✓ cap enforced\n'
printf '✓ allow-list enforced\n'
printf '✓ agent cannot raid account\n'
printf '✓ revocation enforced under load and afterwards\n'
printf '✓ audit reconciles\n'
printf '✓ statement written to agent_wallet/out/statement.html\n\n'

printf 'DEMO PASS\n'
printf '==================================================\n'
