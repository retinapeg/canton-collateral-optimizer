# Settlement spike handoff

## Decision

**Option A — issuer-backed `DemoAsset`: PROVEN.**

**Option B — Canton Coin Token Standard: NOT PROVEN / NOT IMPLEMENTED.**

The proven claim is deliberately narrow: an agent-only `Charge` can atomically
consume valid mandate permission and move a small ledger-native asset, while an
over-cap, disallowed, revoked or mandate-less agent attempt moves no value.

This is not a claim that Canton Coin, fiat, or production money was moved.

The authoritative integration brief requires the final demo to use this
settlement path. Release integration is still blocked until the hardened Daml
commit is wired to the CLI/backend/UI and the complete demo passes twice
against ledger state. This narrow Daml proof is therefore a prerequisite, not
by itself a release ship recommendation.

An independent follow-up audit found that the original proof commit permitted
`owner == agent`, collapsing the intended separation of roles. The follow-up
adds `owner /= agent` invariants to both `MandateProposal` and `Mandate`, an
expiry check at `MandateProposal:Accept`, and an atomic settlement
`ChargeRecord`, which independently enforces the same role separation.
Regression attacks prove neither public permission path nor the audit template
can be created with aliased roles, an expired proposal cannot be accepted, an
agent alone cannot forge a jointly signed audit, and failed settlement attempts
create no record. The narrow Option A decision is PROVEN only for the follow-up
commit and its full green test run; the original commit on its own is
superseded as proof evidence.

Immutable Daml hardening commit:
`08e98952b223299f3537c7a12c83326c11eb2798`.

## Protected baseline

- Repository baseline: `1f8aba1a2dc68f60b5e74fbca8b887e225927103`
- Branch: `agent/settlement-spike`
- Original implementation commit:
  `2dcf171fb2dd2c0f5714b97efbc0421ee3673d5c`
- Required hardening commit:
  `08e98952b223299f3537c7a12c83326c11eb2798`
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

`Settlement.DemoAsset` is a small issuer-backed UTXO/payment claim. Its pitch
label is **DEMO-GBP — issuer-backed demo ledger asset**:

- `issuer` signs every asset and replacement, so the agent cannot mint value
  attributed to that issuer;
- `holder` controls `Give` and `Pay`;
- `viewer` can see the asset but gains no transfer authority;
- `assetId` identifies the demo instrument (`DEMO-GBP` in tests);
- `amount` must be positive.

The display label is fixed, but the repeated-run adapter must bind each
issuance to a unique internal identifier such as `DEMO-GBP:<runId>` and query
only issuer assets carrying that identifier. It must not delete or aggregate
assets from an earlier demo run.

`Pay` is consuming. It archives one input asset and atomically creates a
merchant-owned payment asset plus optional payer change. Every test uses an
issuer party distinct from the wallet owner and agent; the shared fixture names
that issuer `SettlementBank`.

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
7. `Charge` creates exactly one `Settlement.ChargeRecord` containing the
   mandate reference, memo, merchant, cap/spend/allowance snapshot, asset
   issuer/instrument, balance transition and payment/change CIDs, then returns
   three required CIDs plus the optional change CID in four result fields.

The old mandate, old asset, merchant value, payer change, audit record and
successor mandate are consequences in one Daml transaction tree. The ledger
commits the complete tree or none of it. Any failed assertion, missing
authority, inactive input, or failed nested settlement rejects the whole
transaction, including the audit creation.

The `ChargeRecord` payload and the transaction history that created it cannot
be mutated. Its active contract can later be archived with the required
stakeholder authority. An active record alone is not exclusive proof of its
origin because owner and agent acting jointly could create an internally
consistent standalone record; operator verification must inspect the ledger
update/transaction tree and confirm that the returned `auditCid` is a
consequence of the expected `Settlement.Mandate:Charge` exercise.

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
- create a jointly signed `ChargeRecord` as the agent alone;
- substitute an arbitrary payer asset, because the owner-selected exact CID is
  stored in the mandate.

The honest operational boundary is that a service credential with payer
`CanActAs` rights could transfer the payer's own asset directly. A credential
that can act as both owner and agent could also fabricate a standalone audit
record. A real deployment must give the automated client `CanActAs` for the
agent only. The workshop `c8lab.allocate_party()` helper grants a shared user
rights over allocated parties, so that convenience flow must not be used as
evidence of least-authority production configuration. A sandbox run with that
shared credential demonstrates ledger behavior, not production separation of
credential authority.

## Attack tests and results

All results below were produced by `dpm test --show-coverage` on DPM SDK 3.5.7
with OpenJDK 17:

| Test | Attack/proof | Result |
|---|---|---|
| `testSuccessfulChargeMovesLedgerAsset` | Valid agent Charge archives the old mandate and 100-unit payer asset, then creates a 30-unit merchant asset, 70-unit owner change, successor mandate with `spent = 30`, and exactly one audit record returning all result CIDs | PASS |
| `testOverCapChargeMovesNoValue` | After a valid 30-unit payment under an 80 cap, a further 60-unit Charge fails; the current mandate remains at `spent = 30`, owner change remains 70, no second merchant asset is created, and the audit count stays one | PASS |
| `testDisallowedChargeMovesNoValue` | Charge to a party absent from the owner-defined allow-list fails; original mandate and 100-unit asset remain unchanged, the recipient receives nothing, and no audit is created | PASS |
| `testRevokedChargeMovesNoValue` | Owner revokes; stale Charge and direct agent `Pay` both fail; payer asset remains 100, merchant receives nothing, and no audit is created | PASS |
| `testAgentCannotMoveValueWithoutMandate` | Direct agent `Pay`, trusted-issuer mint, forged payer mandate and owner-initiated Charge all fail; mandate/value state is queried unchanged and no audit is created | PASS |
| `testSettlementFailureCannotConsumePermission` | Amount 60 is valid under cap 100 but bound asset contains only 50; nested `Pay` fails and the original mandate remains at `spent = 0` with the 50-unit asset unchanged and no audit | PASS |
| `testRoleAliasCannotCreatePermissionOrAudit` | A single party supplied as both owner and agent cannot create a proposal, mandate or audit record; this protects delegation/audit role separation without denying the holder's legitimate direct asset authority | PASS |
| `testExpiredProposalCannotBeAccepted` | Accept at the exact ledger-time expiry boundary fails; the proposal and 100-unit asset remain active, with no mandate, merchant asset or audit | PASS |
| `testSettlementReleaseSequenceConservesValueAndAudit` | Literal 30 success, 80 over-cap rejection, Merchant-B 10 rejection, owner revocation and stale 1 rejection finish at Owner 70 / Merchant-A 30 / Merchant-B 0 / total 100 with exactly one 30-unit audit | PASS |

The pre-existing tests also remained green:

- `MandateTest:testMandate` — PASS
- `MandateTest:testExpiredMandate` — PASS

`dpm test --show-coverage` reported all six internal templates created and all
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

The clean spike-only DAR reported main package ID
`0c5b5a0774fc93d7b803b8f449148d68865f99fa75e96755067059c2a693e93e`.
That ID is evidence for this branch only, not the final combined release DAR:
merging the separately hardened core changes will produce a different package
ID, which the adapter must derive dynamically after its own clean build.

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

The hardening commit is a delta on the original implementation; do not
cherry-pick it alone onto the baseline. For the complete settlement model and
tests, cherry-pick the inclusive implementation range:

```bash
git cherry-pick 2dcf171^..08e9895
```

The final documentation-only handoff commit is reported separately with the
branch tip. Merging the whole `agent/settlement-spike` branch naturally includes
the same history.

Do not merge or copy:

- ignored `.daml/` build caches or generated DARs;
- anything from the temporary upstream clone used for the Option B read-only
  review;
- any sequential `Charge` plus `c8lab.transfer()` choreography;
- any claim that `DemoAsset` is Canton Coin or production money.

No stable Mandate, backend, UI, deployment or demo-script file is part of this
commit.
