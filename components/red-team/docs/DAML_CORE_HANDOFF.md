# Daml core integration handoff

## Branch and scope

- Branch: `agent/daml-core`
- Baseline: `1f8aba1a2dc68f60b5e74fbca8b887e225927103`
- Daml package: `track-exercises/d1-spend-limited-wallet`
- Package name/version: `d1-spend-limited-wallet` / `0.0.1`
- Clean-build main package ID:
  `53cd4f6306f6403ddcf7611a667b961a5a5155c8f9bc784fb3c0c6fe971e2d96`
- Module: `Mandate`
- No Python, UI, deployment, credential or unrelated business-model file was
  modified.

Stable package-name template references for the JSON Ledger API are:

- `#d1-spend-limited-wallet:Mandate:MandateProposal`
- `#d1-spend-limited-wallet:Mandate:Mandate`
- `#d1-spend-limited-wallet:Mandate:ChargeRecord`

The deployed package-ID form is
`<uploaded-package-id>:Mandate:<template-name>`. Prefer the package-name form
when supported so a rebuilt DAR's content hash does not need to be hard-coded.

## Create and exercise shapes

Ledger JSON represents `Party` and `ContractId` as strings, `Decimal` as a JSON
string, `Time` as an ISO-8601 string and Daml lists as JSON arrays.

### Create `MandateProposal`

```json
{
  "owner": "<Party>",
  "agent": "<Party>",
  "cap": "100.0",
  "expiresAt": "<ISO-8601 Time>",
  "allowedCounterparties": ["<Party>"],
  "mandateReference": "MANDATE-DEMO-001"
}
```

The owner is the signatory and the agent is an observer.

### Exercise `MandateProposal.Accept`

- Controller: agent
- Argument: `{}`
- Return: `ContractId Mandate` (a JSON contract-ID string)
- Effect: consumes proposal and creates an active Mandate with `spent = 0.0`

`Reject` is also agent-controlled, takes `{}` and returns unit.

### Active `Mandate` payload

```json
{
  "owner": "<Party>",
  "agent": "<Party>",
  "cap": "100.0",
  "spent": "0.0",
  "expiresAt": "<ISO-8601 Time>",
  "allowedCounterparties": ["<Party>"],
  "mandateReference": "MANDATE-DEMO-001"
}
```

Owner and agent are signatories.

### Exercise `Mandate.Charge`

- Controller: agent only
- Argument:

```json
{
  "counterparty": "<Party>",
  "amount": "30.0",
  "memo": "Invoice INV-001"
}
```

- Return (`ChargeResult`):

```json
{
  "mandateCid": "<replacement Mandate ContractId>",
  "auditCid": "<ChargeRecord ContractId>"
}
```

The choice is consuming. Clients must replace any cached active mandate ID with
`mandateCid`; the old ID is intentionally unusable. Do not duplicate any Daml
policy as client-side authorisation. Deliberately invalid requests should be
submitted when demonstrating that the ledger rejects them.

### Exercise `Mandate.Revoke`

- Controller: owner only
- Argument: `{}`
- Return: unit
- Effect: consumes Mandate and creates no replacement

### Exercise `Mandate.Adjust`

- Controllers: owner and agent together
- Argument: `{"newCap":"200.0"}`
- Return: replacement `ContractId Mandate`
- Submission: both parties in `actAs`; no `readAs` is required merely to meet
  the controller policy

### `ChargeRecord` payload

```json
{
  "owner": "<Party>",
  "agent": "<Party>",
  "counterparty": "<Party>",
  "amount": "30.0",
  "transactionTime": "<ISO-8601 Time>",
  "memo": "Invoice INV-001",
  "mandateReference": "MANDATE-DEMO-001",
  "previousSpent": "0.0",
  "newSpent": "30.0",
  "cap": "100.0",
  "remainingAllowance": "70.0",
  "expiresAt": "<ISO-8601 Time>",
  "allowedCounterpartiesAtCharge": ["<Party>"]
}
```

Owner and agent are signatories. Counterparties are not observers because the
audit exposes the complete permission snapshot.

## Integration rules that must not move off ledger

- Use the returned replacement CID after every successful consuming choice.
- Treat ledger rejection as authoritative for expiry, amount, allow-list, cap,
  controller, revocation and stale-ID decisions.
- Display `remainingAllowance` from the audit or calculate `cap - spent` from
  the active Mandate; do not maintain a separate client-side spend counter.
- A Charge records authorisation only. It does not transfer Canton Coin or any
  other real asset.

## Verification status

Final local gates from the package root:

- `dpm clean`: pass;
- `dpm build`: pass, DAR created;
- `dpm test`: pass, all 17 named Scripts are `ok`;
- `dpm test --show-coverage`: pass, all 3 templates created and all 5 declared
  business choices exercised; the only 3 uncovered choices are implicit
  `Archive` choices;
- `git diff --check`: pass;
- deprecated `submitMulti`: absent.

The expected single-package `daml-script` warning remains; no multi-package
restructure was introduced solely to silence it. The immutable commit SHA is
reported with the branch handoff.
