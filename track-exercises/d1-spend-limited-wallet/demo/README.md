# Spend-limited wallet demo operator

This directory contains the dependency-free P0 terminal producer for the
spend-limited AI wallet. It calls the authoritative `scripts/wallet_cli.py`
ordinary subcommands and renders their JSON; it does not import the ledger
client or implement wallet policy.

## Frozen integration status

This commit is the **pre-settlement P0 base** requested by the release
integrator. It is intentionally frozen before the final settlement overlay is
available.

- It proves the operator/CLI boundary, deterministic fixture development path,
  mode mapping, error handling, timeout selection, and the current canonical
  status/audit projection.
- It does **not** prove or claim that a ledger asset moved.
- It does **not** claim `DEMO-GBP`, Canton Coin, GBP, a stablecoin, escrow, or
  production money settlement.
- The final settlement engineer must adapt the result projection and tests to
  the committed `Settlement.Mandate:Charge` CLI envelope before this can be the
  final pitch build.
- The optional browser P1 was deliberately excluded. The terminal remains the
  reliable authority and avoids introducing a loopback mutation endpoint before
  origin/CSRF controls and final settlement state are ready.

## Start from the repository root

Local Canton sandbox target:

```sh
python3 track-exercises/d1-spend-limited-wallet/demo/run_demo.py
```

DevNet target:

```sh
python3 track-exercises/d1-spend-limited-wallet/demo/run_demo.py --target devnet
```

`--target` is a fail-closed expectation check. It does not select or relabel the
CLI's environment. `wallet_cli.py` selects `DEVNET` when `C8_IDP` is non-empty
and `SANDBOX` otherwise, according to `docs/CLI_CONTRACT.md`.

Explicit UI-development fixture:

```sh
python3 track-exercises/d1-spend-limited-wallet/demo/run_demo.py --target fixture --no-pause
```

The fixture is permanently labelled:

```text
OFFLINE FIXTURE
UI DEVELOPMENT ONLY — NOT LEDGER ENFORCEMENT
SIMULATED UI RESPONSE — NO LEDGER
```

It is a fixed ordered replay from `demo/fixtures/story.json`. It does not
evaluate cap, expiry, allow-list, active/revoked state, or any other policy.
There is no automatic live/sandbox-to-fixture fallback.

## Authoritative command interface

The operator consumes these CLI-owned subcommands:

```text
health
upload-dar
list-parties
setup-demo
create-mandate
status
charge --counterparty VALUE --amount VALUE --memo VALUE
revoke
audit
run-demo
```

`run_demo.py` performs a quick `health` preflight, verifies the returned mode,
then runs the CLI-owned `run-demo` command. That CLI command owns the complete
security sequence and its final proof. The operator does not reproduce ledger
or Daml logic.

Every handled CLI command returns one UTF-8 JSON document on stdout with these
stable top-level fields:

```json
{
  "schemaVersion": 1,
  "ok": true,
  "command": "health",
  "mode": "SANDBOX",
  "result": {}
}
```

The demo validates and maps machine modes as follows:

| CLI machine mode | Human-facing label |
|---|---|
| `DEVNET` | `LIVE DEVNET` |
| `SANDBOX` | `LOCAL CANTON SANDBOX` |
| explicit local fixture only | `OFFLINE FIXTURE` |

Any unknown mode or expected/actual mode mismatch is a protocol error. It is
never silently relabelled.

Individual deliberate `charge` attacks may return `ok: false` and exit `1`
because Canton rejected the submission. The adapter treats a response as the
expected ledger result only with scenario-specific definitive evidence:

- Merchant-A / `80`: recognized assertion `charge would exceed the cap`;
- Merchant-B / `10`: recognized counterparty/merchant allow-list assertion;
- post-revocation Merchant-A / `1`: HTTP 4xx plus `CONTRACT_NOT_FOUND` and
  `definiteAnswer: true`.

Authentication, authorization, configuration, usage, party, transport,
timeout, and ambiguous errors are never presented as policy rejections.

## Reliability behavior

- Quick JSON commands have a 30-second default timeout.
- The multi-transaction `run-demo` command has a separate 300-second default
  timeout. A fresh DAR upload, party setup, and ledger sequence are not killed
  by the quick-command cap.
- No mutating command is automatically retried.
- A mutation timeout is reported as `OUTCOME UNKNOWN`; the operator tells the
  user to query status/audit before deciding whether a retry is safe.
- Subprocesses use argument arrays with `shell=False`.
- CLI stdout must be UTF-8 JSON. Diagnostics stay on stderr.
- Terminal output uses no curses, terminal clearing, ANSI requirement, or
  third-party package.
- `run-demo` renders revocation before the stale one-unit attempt.

## Exact unavailable-ledger behavior

The default branch baseline does not contain the DevNet-owned
`scripts/wallet_cli.py` or `docs/CLI_CONTRACT.md`. Until those are integrated,
the root start command exits `2` after printing a stable screen containing:

```text
DEMO UNAVAILABLE
MODE: LOCAL CANTON SANDBOX
ENVIRONMENT NOT READY / NO LEDGER RESULT
No enforcement claim is being made.
There is no automatic fallback to OFFLINE FIXTURE.
```

Other failures are separated deliberately:

| Failure | Display |
|---|---|
| missing/unreadable CLI | `ENVIRONMENT NOT READY / NO LEDGER RESULT` |
| unreachable CLI/ledger before a definitive result | `LEDGER UNAVAILABLE / NO LEDGER RESULT` |
| mutation timeout or ambiguous mutation failure | `LEDGER UNAVAILABLE / OUTCOME UNKNOWN` |
| invalid JSON, unknown mode, or mode mismatch | `CLI PROTOCOL ERROR / NO LEDGER RESULT` |
| definitive expected attack rejection | rendered as the returned ledger rejection |

No cached or fixture state is substituted after failure.

## Final settlement adaptation gate

The final release must not use the older `Mandate.Mandate:Charge` result as
settlement evidence. It must integrate the hardened settlement path:

```text
Settlement.Mandate:Charge
  -> nested Settlement.DemoAsset:Pay
  -> successor mandate CID
  -> merchant payment asset CID
  -> optional owner change asset CID
  -> atomic ChargeRecord CID
```

After the settlement CLI schema and SHA land, update the operator projection
and captured contract tests so the final live/sandbox display shows, from
fresh CLI queries:

```text
DEMO-GBP — issuer-backed demo ledger asset
Owner asset balance:      70
Merchant-A asset balance: 30
Merchant-B asset balance: 0
Mandate spent:            30
Mandate remaining:        70
```

After every deliberate rejection, re-query both permission and value state and
show those values unchanged. After revocation, submit the stale one-unit charge
to Canton and re-query again.

Each setup/reset run must use a unique internal instrument identity such as
`DEMO-GBP:<run-id>` while retaining the exact human display label above. This
prevents balances from prior immutable demo runs being aggregated into the
current run's 70/30/0 statement.

Use the verb **binds**, not *escrows*: the owner binds the exact asset contract
ID to the mandate but remains holder/controller and can exercise `Pay` or
`Give` directly. Doing so makes the mandate's bound asset CID stale, causing a
later agent `Charge` to roll back. This limitation must stay visible in the
final pitch documentation.

The only permitted asset claim is **issuer-backed demo ledger asset**. Canton
Coin remains not implemented.

## Tests

From the exercise directory:

```sh
python3 -m unittest discover -s demo/tests -v
python3 -m py_compile demo/wallet_client.py demo/presentation.py demo/run_demo.py
python3 -m json.tool demo/fixtures/story.json >/dev/null
```

The P0 tests cover:

- `DEVNET`/`SANDBOX` machine-mode mapping and fail-closed unknown/mismatch;
- 300-second `run-demo` versus short quick-command timeout selection;
- exact one-time forwarding of all three malicious `charge` argument lists;
- definitive ledger rejection versus transport failure;
- canonical `status.result.activeMandate` and `audit.result.records` projection,
  including `remainingAllowance` and `transactionTime`;
- revoke-before-post-revocation story ordering;
- missing CLI error screen and absence of automatic fixture fallback.

No dependency install is required.
