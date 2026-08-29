#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
source "$ROOT_DIR/scripts/demo_common.sh"

demo_on_exit() {
  local exit_status="$?"
  local cleanup_status="0"
  trap - EXIT
  demo_cleanup_canton || cleanup_status="$?"
  demo_release_lock || true
  if [[ "$exit_status" -eq 0 && "$cleanup_status" -ne 0 ]]; then
    exit_status="$cleanup_status"
  fi
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
demo_find_system_python
demo_configure_toolchain

printf '==================================================\n'
printf 'CANTON COLLATERAL OPTIMIZER DEMO\n'
printf '==================================================\n\n'

printf 'BOOTSTRAP\n'
printf '✓ Python %s (.venv)\n' "$($DEMO_PYTHON --version 2>&1 | awk '{print $2}')"
printf '✓ DPM (collateral SDK %s; wallet SDK %s)\n' \
  "$(demo_selected_sdk "$DEMO_COLLATERAL_DIR")" \
  "$(demo_selected_sdk "$DEMO_WALLET_DIR")"
printf '✓ Canton %s available\n\n' "$(demo_canton_version "$DEMO_COLLATERAL_DIR")"

printf 'FRESH CANTON LEDGER\n'
demo_start_fresh_canton
printf '✓ Owned sandbox ready at %s\n' "$DEMO_LEDGER_URL"
printf '✓ Dedicated ports: %s-%s and %s\n' \
  "$DEMO_LEDGER_GRPC_PORT" "$DEMO_MEDIATOR_ADMIN_PORT" "$DEMO_LEDGER_HTTP_PORT"
demo_upload_dar "collateral" "$DEMO_COLLATERAL_DAR"
demo_upload_dar "wallet" "$DEMO_WALLET_DAR"
printf '✓ Collateral and wallet packages loaded\n\n'

# This bridge is the only optimiser invocation in the launcher. It prints the
# optimiser result, deterministic ledger mapping, contract receipts, ledger
# query, reconciliation, and authorization proof as one coherent [1]-[6] flow.
if ! "$DEMO_PYTHON" -B -m backend.allocation_demo \
  --base-url "$DEMO_LEDGER_URL" \
  2>&1 | tee "$DEMO_RUN_DIR/allocation-demo.log"; then
  demo_fail "The optimizer-to-ledger institutional allocation failed."
  exit 1
fi

for required_evidence in \
  '✓ OPTIMIZER PASS' \
  '✓ DAML SMART CONTRACTS COMMITTED TO CANTON' \
  '✓ OPTIMIZER-TO-LEDGER END-TO-END PASS' \
  '✓ recipient authority enforced'
do
  if ! grep -F "$required_evidence" "$DEMO_RUN_DIR/allocation-demo.log" >/dev/null; then
    demo_fail "Institutional allocation evidence is incomplete: $required_evidence"
    exit 1
  fi
done

printf '\nSPEND-LIMITED WALLET\n\n'
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

printf 'FULL DEMO PASS\n'
printf '==================================================\n'
