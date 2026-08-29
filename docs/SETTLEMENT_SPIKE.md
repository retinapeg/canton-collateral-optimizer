# Settlement spike handoff

## Decision

**Option A — issuer-backed `DemoAsset`: PROVEN.**

**Option B — Canton Coin Token Standard: NOT PROVEN / NOT IMPLEMENTED.**

The proven claim is deliberately narrow: an agent-only `Charge` can atomically
consume valid mandate permission and move a small ledger-native asset, while an
over-cap, disallowed, revoked or mandate-less agent attempt moves no value.

This is not a claim that Canton Coin, fiat, or production money was moved.

## Protected baseline

- Repository baseline: `1f8aba1a2dc68f60b5e74fbca8b887e225927103`
- Branch: `agent/settlement-spike`
- The existing `Mandate.daml`, `MandateTest.daml`, backend and demo scripts were
  not modified.
- The original dirty `main` checkout was not touched. Work was performed in an
  isolated worktree.

## Sources read before implementation

The spike started from the Cantor8 toolkit's:

- `daml-starter/daml/Mandate.daml`
- `daml-starter/daml/Iou.daml`
- `daml-starter/daml/Test.daml`
- `daml-starter/README.md`
- `c8lab.py`, especially `submit()` and `transfer()`
- `README.md`, `API.md` and `TROUBLESHOOTING.md` token-registry notes

The latest remote `Cantor8/hackathon-toolkit` `main` observed during the bounded
Option B review was `4e836376654ae97c8cb86e149dc5b1a39bc549e7`.
Its `c8lab.py` was byte-identical to the initially inspected local version; the
new remote commit extended scanner documentation rather than the transfer
implementation.

## Asset used

`Settlement.DemoAsset` is a small issuer-backed UTXO/payment claim:

- `issuer` signs every asset and replacement, so the agent cannot mint value
  attributed to that issuer;
- `holder` controls `Give` and `Pay`;
- `viewer` can see the asset but gains no transfer authority;
- `assetId` identifies the demo instrument (`DEMO-GBP` in tests);
- `amount` must be positive.

`Pay` is consuming. It archives one input asset and atomically creates a
merchant-owned payment asset plus optional payer change. The test issuer is a
separate `SettlementBank` party, not the wallet owner or agent.

This must be described in the pitch as an **issuer-backed mock/demo asset**, not
as Canton Coin, GBP, a stablecoin, or a redeemable production claim.

## Atomicity and authority argument

The owner-created proposal binds the exact payer asset contract ID, issuer,
instrument, amount and allow-list. The agent accepts it to create
`Settlement.Mandate`, whose signatories are `owner, agent`.

The only agent-controlled value path is `Settlement.Mandate:Charge`:

1. The root submission acts as the agent, the only `Charge` controller.
2. `Charge` checks ledger time, positive amount, owner-defined allow-list and
   cumulative cap in Daml.
3. It fetches the exact asset CID stored in the mandate and verifies issuer,
   holder, viewer, instrument and amount.
4. In the same choice body it nested-exercises the owner-controlled,
   consuming `DemoAsset:Pay` choice.
5. `Pay` archives the payer input and creates the merchant asset and payer
   change.
6. `Charge` creates the successor mandate with increased `spent` and binds it
   to the returned change CID.

The old mandate, old asset, merchant value, payer change and successor mandate
are consequences in one Daml transaction tree. The ledger commits the complete
tree or none of it. Any failed assertion, missing authority, inactive input, or
failed nested settlement rejects the whole transaction.

Revocation is owner-controlled and consumes only the mandate. The current payer
asset remains active, but the observer-only agent cannot exercise it directly.

Two concurrent charges also cannot both commit because both target the same
consuming mandate and exact bound asset CIDs.

## Backend-bypass boundary

The Daml proof uses agent-only submissions for `Charge`. With agent authority
alone, a backend cannot:

- exercise `DemoAsset:Pay`, which is controlled by the payer/holder;
- mint a `DemoAsset` attributed to the trusted issuer;
- create a payer mandate, which requires the owner and agent signatories;
- exercise `Charge` as the owner or another party;
- substitute an arbitrary payer asset, because the owner-selected exact CID is
  stored in the mandate.

The honest operational boundary is that a service credential with payer
`CanActAs` rights could transfer the payer's own asset directly. A real
deployment must give the automated client `CanActAs` for the agent only. The
workshop `c8lab.allocate_party()` helper grants a shared user rights over
allocated parties, so that convenience flow must not be used as evidence of
least-authority production configuration.

## Attack tests and results

All results below were produced by `dpm test --show-coverage` on DPM SDK 3.5.7
with OpenJDK 17:

| Test | Attack/proof | Result |
|---|---|---|
| `testSuccessfulChargeMovesLedgerAsset` | Valid agent Charge archives the old mandate and 100-unit payer asset, then creates a 30-unit merchant asset, 70-unit owner change and successor mandate with `spent = 30` | PASS |
| `testOverCapChargeMovesNoValue` | After a valid 30-unit payment under an 80 cap, a further 60-unit Charge fails; the current mandate remains at `spent = 30`, owner change remains 70 and no second merchant asset is created | PASS |
| `testDisallowedChargeMovesNoValue` | Charge to a party absent from the owner-defined allow-list fails; original mandate and 100-unit asset remain unchanged and the recipient receives nothing | PASS |
| `testRevokedChargeMovesNoValue` | Owner revokes; stale Charge and direct agent `Pay` both fail; payer asset remains 100 and merchant receives nothing | PASS |
| `testAgentCannotMoveValueWithoutMandate` | Direct agent `Pay`, trusted-issuer mint, forged payer mandate and owner-initiated Charge all fail; mandate/value state is queried and unchanged | PASS |
| `testSettlementFailureCannotConsumePermission` | Amount 60 is valid under cap 100 but bound asset contains only 50; nested `Pay` fails and the original mandate remains at `spent = 0` with the 50-unit asset unchanged | PASS |

The pre-existing tests also remained green:

- `MandateTest:testMandate` — PASS
- `MandateTest:testExpiredMandate` — PASS

`dpm test --show-coverage` reported all five internal templates created and all
settlement attack scripts successful. Choice percentage is not the security
gate: unused convenience/archive/adjust/reject choices account for the
unexercised choices.

## Exact verification commands

Run from `track-exercises/d1-spend-limited-wallet`:

```bash
export JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
export PATH=/Users/leonardaarons-ditson/.dpm/bin:$JAVA_HOME/bin:$PATH

dpm clean
dpm build
dpm test
dpm test --show-coverage
git diff --check
```

The only warnings observed were pre-existing:

- deprecated `submitMulti` in `daml/MandateTest.daml`;
- the existing single-package warning that a template package also depends on
  `daml-script`.

Neither warning was changed in this settlement-owned spike.

## Option B finding — not a proof

The current `c8lab.transfer()` performs:

1. an off-ledger registry request for `factoryId`, choice context and disclosed
   contracts;
2. a separate ledger submission exercising `TransferFactory_Transfer` as the
   token sender.

Exercising `Mandate:Charge` and then calling `c8lab.transfer()` would be two
transactions. It is not atomic and was not added.

The clean next Canton Coin architecture is:

1. Use the registry only as a read-only preflight to obtain the factory,
   context and disclosures.
2. Submit one root command acting only as the agent: exercise `Mandate:Charge`
   and pass the prepared transfer arguments to it.
3. Nested-exercise `TransferFactory_Transfer` inside `Charge` so token holdings
   and mandate permission share one transaction.
4. Inspect the on-ledger transfer result. Recreate the spent mandate only for
   `Completed`; abort on `Pending` or `Failed`, which rolls back the whole
   transaction. An off-ledger registry `transferKind = offer` is not merchant
   payment.
5. Prove the same attack suite against actual holding CIDs and prove the
   submitting user lacks payer `CanActAs` rights.

No Canton Coin package was added, no registry or DevNet call was made, no
credentials were read, and no Canton Coin result should be claimed from this
branch.

## Cherry-pick boundaries

Safe to cherry-pick together:

- `track-exercises/d1-spend-limited-wallet/daml/Settlement.daml`
- `track-exercises/d1-spend-limited-wallet/daml/SettlementTest.daml`
- `docs/SETTLEMENT_SPIKE.md`

Do not merge or copy:

- ignored `.daml/` build caches or generated DARs;
- anything from the temporary upstream clone used for the Option B read-only
  review;
- any sequential `Charge` plus `c8lab.transfer()` choreography;
- any claim that `DemoAsset` is Canton Coin or production money.

No stable Mandate, backend, UI, deployment or demo-script file is part of this
commit.
