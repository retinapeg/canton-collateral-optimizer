# Three-minute spend-limited wallet story

## Release gate

The final pitch may use this story only after the committed CLI demonstrably
exercises `Settlement.Mandate:Charge`, nested `Settlement.DemoAsset:Pay`, and
returns fresh permission, asset-balance, and `Settlement.ChargeRecord` state.

The frozen P0 producer commit predates that settlement CLI overlay. It must not
be presented as settlement evidence by itself.

## Before the clock starts

1. Run the terminal preflight in the intended environment.
2. Confirm the screen says exactly `LIVE DEVNET` or `LOCAL CANTON SANDBOX`.
3. Confirm it does not say `OFFLINE FIXTURE`.
4. Confirm the CLI reports the hardened Settlement package/DAR and the intended
   local parties.
5. Confirm the operator identifies the asset exactly as:

   ```text
   DEMO-GBP — issuer-backed demo ledger asset
   ```

   The CLI should scope the current run with a unique internal asset identity,
   such as `DEMO-GBP:<run-id>`, so immutable assets from earlier runs are not
   aggregated into this run's displayed balances.

6. If health, mode, package, rights, permission state, asset state, or audit
   refresh fails, stop. Never switch to the fixture during the pitch.

## 0:00–0:30 — Establish the authority boundary

Say:

> The owner gives this agent a 100-unit spending mandate, binds one exact
> DEMO-GBP asset contract to it, and permits only Merchant-A.

Show the CLI-returned state:

- owner and agent;
- `DEMO-GBP — issuer-backed demo ledger asset`;
- cap `100`;
- spent `0`;
- remaining `100`;
- active state and expiry;
- Merchant-A as the sole allowed merchant;
- owner asset balance `100`;
- Merchant-A asset balance `0`;
- Merchant-B asset balance `0`.

Use **binds**, not *escrows*. The owner remains the asset holder/controller and
can exercise `DemoAsset:Pay` or `DemoAsset:Give` directly. Doing so stales the
asset CID bound into the mandate, so a later agent charge rolls back. This is an
explicit limitation of the demo architecture.

Do not describe DEMO-GBP as Canton Coin, fiat GBP, a stablecoin, redeemable
money, or a production claim.

## 0:30–0:55 — The valid purchase

Submit the CLI command corresponding to:

```text
charge --counterparty Merchant-A --amount 30 --memo "approved demo charge"
```

Do not update the screen optimistically. Wait for Canton, then re-query
permission state, all three asset balances, and audit records.

Show the returned state:

```text
Owner asset balance:      70
Merchant-A asset balance: 30
Merchant-B asset balance: 0
Mandate spent:            30
Mandate remaining:        70
```

Show the immutable 30-unit `Settlement.ChargeRecord`, merchant payment asset
CID, owner change asset CID, successor mandate CID, and committed update ID.
Explain that `Settlement.Mandate:Charge` nested-exercised `DemoAsset:Pay`, so
permission consumption, value movement, successor permission, and audit record
committed atomically or not at all.

## 0:55–1:25 — Prompt-injection over-cap attack

Say:

> A prompt injection now asks the agent for another 80.

Submit Merchant-A / `80` unchanged to the CLI. The operator must not compare it
with remaining balance, disable it, warn-and-cancel it, or synthesize a local
rejection.

Only after Canton responds, show the definitive ledger rejection. Re-query
permission, value, and audit, then show all are unchanged:

```text
Owner 70 · Merchant-A 30 · Merchant-B 0 · spent 30 · remaining 70
Audit: one accepted record for 30
```

## 1:25–1:55 — Disallowed-merchant attack

Submit Merchant-B / `10` unchanged to the CLI.

Only after Canton responds, show the allow-list rejection. Re-query and show
the same unchanged permission, balances, and one-record audit. Merchant-B must
still hold `0`.

## 1:55–2:25 — Owner revocation

Submit `revoke` as the owner and show its committed update ID. Re-query and
show the permission as revoked while the owner change asset remains on-ledger.

Do not claim revocation destroys or escrows the asset. It consumes the agent's
mandate permission; the owner remains holder/controller of the current asset.

## 2:25–2:45 — Stale one-unit attack

After revocation, submit Merchant-A / `1` unchanged against the saved stale
mandate contract. The CLI must send it to Canton; the UI must not reject it from
cached `REVOKED` state.

Only after Canton responds, show the definitive stale-contract rejection.
Re-query permission, value, and audit once more:

```text
Owner 70 · Merchant-A 30 · Merchant-B 0 · spent 30 · remaining 70
Audit: one accepted record for 30
```

## 2:45–3:00 — Close on the proof

Show the final accepted-spend audit and say:

> Only the valid 30-unit Merchant-A payment entered the immutable statement.
> The three malicious requests were sent to Canton, rejected there, and moved
> no additional demo asset.

Leave these on screen:

- exact live/sandbox mode label;
- `DEMO-GBP — issuer-backed demo ledger asset`;
- owner `70`, Merchant-A `30`, Merchant-B `0`;
- spent `30`, remaining `70`;
- revoked permission state;
- the sole 30-unit `Settlement.ChargeRecord`;
- the most recent definitive stale-contract ledger result.

## Failure language

Use the following distinctions exactly:

- Definitive Canton policy/stale-contract response: `REJECTED BY LEDGER`.
- Missing CLI/configuration: `ENVIRONMENT NOT READY / NO LEDGER RESULT`.
- Unreachable ledger: `LEDGER UNAVAILABLE / NO LEDGER RESULT`.
- Mutation timeout or ambiguous failure: `LEDGER UNAVAILABLE / OUTCOME UNKNOWN`.
- Malformed JSON or unknown/mismatched mode: `CLI PROTOCOL ERROR / NO LEDGER RESULT`.
- Fixture: `SIMULATED UI RESPONSE — NO LEDGER`.

Never call a timeout, HTTP connection failure, authentication failure, malformed
response, or canned fixture response a ledger rejection.

## Security invariants for the presentation layer

- Never implement cap, expiry, allow-list, asset-balance, or revoked-state
  authorization in the operator.
- Never disable or pre-filter a malicious action from cached state.
- Never calculate authoritative remaining allowance or asset balances locally.
- Never append, delete, edit, or clear an audit record in the UI.
- Never automatically retry a mutation with an unknown outcome.
- Preserve CLI-returned audit ordering and immutable identifiers.
- Re-query both permission and value after every attack.
- Never fall back from a ledger mode to `OFFLINE FIXTURE`.
