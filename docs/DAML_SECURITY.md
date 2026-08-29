# Daml security model: spend-limited AI wallet

This package is a ledger-enforced spending authorisation and audit trail. The
client may display policy state and submit commands, but it is not a security
boundary: every rule that decides whether a charge is allowed is enforced in
the `Mandate.Charge` choice.

## Contract lifecycle

1. The owner creates `MandateProposal` as signatory; the proposed agent is an
   observer. The proposal carries the cap, expiry, allow-list and stable
   `mandateReference` that will be copied to the active mandate and audits.
2. The agent exercises `Accept`. Acceptance checks ledger time and creates a
   jointly signed `Mandate` with `spent = 0.0`. The agent may instead exercise
   `Reject`, and the owner retains the implicit archive mechanism for a pending
   proposal.
3. The agent exercises consuming choice `Charge`. Daml reads ledger time and
   checks expiry, positive amount, allow-list membership, cumulative cap and a
   nonempty memo before any create. A successful transaction archives the old
   Mandate, creates its replacement with increased `spent`, creates exactly one
   `ChargeRecord`, and returns both new contract IDs as `ChargeResult`.
4. Either a later `Charge` or a jointly authorised `Adjust` consumes the active
   Mandate and returns its replacement contract ID. This linear contract-ID
   chain serialises spending; two commands against the same old ID cannot both
   succeed.
5. The owner may exercise consuming choice `Revoke` alone. It creates no
   replacement. The archived ID is immediately stale, and every later attempt
   to exercise `Charge` on it is rejected by the ledger.

“Immediate” is ledger-ordering precise: after the revocation transaction is
committed, no later ledger-ordered transaction can exercise that consumed CID.
A valid charge committed before revocation remains committed and audited.

## Ledger-enforced invariants

### Proposal and active mandate

- `owner` and `agent` must be different parties. This prevents the bilateral
  cap-adjustment policy collapsing into one identity.
- `cap` must be strictly positive.
- `spent` must be nonnegative and no greater than `cap`.
- `mandateReference` must be nonempty.
- `Accept` requires `now < expiresAt`, so an expired proposal cannot become an
  active-but-unusable mandate.
- `Mandate` is signed by both owner and agent. The proposal/acceptance flow
  supplies their consent without requiring them to submit simultaneously.

### Charge

- Controller: `agent` only. Being the owner/signatory does not grant Charge
  authority.
- Time: the ledger's `getTime`, not a client timestamp, must be strictly before
  expiry. `transactionTime` records this Daml ledger time; it is not a
  client-provided wall-clock or participant record-time value.
- Amount: must be greater than zero.
- Counterparty: must be a member of the on-ledger allow-list.
- Total cap: `newSpent = spent + amount` must be no greater than `cap`.
- Memo: must be nonempty for an intelligible audit display.
- Atomicity: all assertions occur before the successor and audit creates. Daml
  transaction rollback means a failed assertion archives nothing and creates
  nothing.

The three judge-facing enforcement lines are in
`track-exercises/d1-spend-limited-wallet/daml/Mandate.daml`:

- Expiry, exact source line 54:
  `assertMsg "mandate expired" (now < expiresAt)`
- Allow-list, exact source line 56:
  `assertMsg "counterparty is not allow-listed" (counterparty \`elem\` allowedCounterparties)`
- Total cap, exact source line 57:
  `assertMsg "charge would exceed the cap" (newSpent <= cap)`

### Cap adjustment

- `Adjust` requires both `owner` and `agent` as controllers.
- `newCap` must remain strictly positive.
- `newCap` may never be less than the amount already spent.
- If the agent will not co-authorise a reduction, the owner can revoke
  immediately and propose a new, lower-cap mandate. The owner is never trapped
  in an unwanted active authorisation.

### Audit record

Every `ChargeRecord` contains:

- owner, agent and charged counterparty;
- amount, ledger transaction time and human-readable memo;
- mandate reference;
- previous spent, new spent, cap and remaining allowance;
- the expiry and full allow-list snapshot in force when the charge succeeded.

Its template invariant independently checks all arithmetic and permission
context: positive amount, `newSpent = previousSpent + amount`, new spent at or
below cap, exact remaining allowance, charge time before expiry and
counterparty membership in the snapshotted allow-list. Owner and agent are
signatories, so the agent alone cannot directly fabricate a record requiring
the owner's authority. Counterparties are deliberately not observers because
the record contains the entire allow-list and budget policy.

`ChargeRecord` has no mutable business choice. Like every Daml template it has
an implicit `Archive` choice, so “immutable” means its payload and ledger
history cannot be edited; it does not mean the active contract can never be
jointly archived. The historical create/archive events remain in the ledger.

## Test-to-requirement matrix

| Requirement or attack | Daml Script proof |
| --- | --- |
| Allow-listed charge under cap succeeds | `testAllowListedChargeSucceeds` |
| Two charges update cumulative spent correctly | `testCumulativeSpendingAcrossTwoCharges` |
| Charge that takes total above cap fails | `testChargeOverCapFails` |
| Non-allow-listed counterparty fails | `testNonAllowListedCounterpartyFails` |
| Zero amount fails | `testZeroAmountFails` |
| Negative amount fails | `testNegativeAmountFails` |
| Party other than agent cannot Charge | `testOnlyAgentCanCharge` (owner is a visible stakeholder, isolating controller failure) |
| Expired mandate fails using controlled ledger time | `testExpiredMandateFailsAfterLedgerTimeAdvance` (sets time to the exact expiry boundary) |
| Owner-only revocation succeeds | `testOwnerCanRevoke` |
| Revoked/stale contract ID cannot be charged | `testStaleMandateCidFailsAfterRevocation` |
| Cap below spent fails | `testAdjustCapBelowSpentFails` |
| Cap adjustment requires intended authorisation | `testCapAdjustmentAuthorization` (owner-only and agent-only fail; joint `actAs` succeeds) |
| Failed charges create no audits; successful charge creates one | `testOnlySuccessfulChargesCreateAuditRecords` |
| Audit fields and remaining allowance are exact | `testAuditFieldsAndRemainingAllowance` |
| Charge exactly at cap succeeds (`<=`, not `<`) | `testChargeExactlyAtCapSucceeds` |
| Proposal rejection remains functional | `testProposalCanBeRejected` |
| Exact release demo/attack sequence | `testReleaseDemoAttackSequence` |

For compatibility with the authoritative baseline gate, `testMandate` delegates
to `testReleaseDemoAttackSequence` and `testExpiredMandate` delegates to
`testExpiredMandateFailsAfterLedgerTimeAdvance`. The compatibility names add no
new business logic and retain the stronger granular scripts as the primary
requirement proofs.

The release-sequence test proves, in one ledger trace: cap 100; only Merchant-A
allowed; Merchant-A 30 succeeds and leaves 70; Merchant-A 80 fails;
Merchant-B 10 fails; owner revokes; a 1-unit charge on the revoked ID fails;
and exactly one 30-unit `ChargeRecord` remains.

All deliberately invalid submissions use `submitMustFail`. Therefore a green
test script means the ledger rejected the attack as expected; it does not mean
the invalid transaction succeeded.

## What is and is not actual money

Represented on ledger:

- a Decimal-denominated authorisation cap;
- cumulative authorised charge amounts;
- remaining allowance and immutable audit facts;
- the parties, deadline, allow-list and human references governing that
  authorisation.

Not represented or moved:

- Canton Coin or any token-standard asset;
- a bank balance, escrow balance or custody account;
- settlement, clearing, payment finality, currency code or exchange rate;
- proof that an off-ledger merchant received funds.

A successful `Charge` is therefore an authorised accounting event and audit
record, not a transfer of real value. Token transfer integration is a later,
separate step and must not be claimed in the demo.
