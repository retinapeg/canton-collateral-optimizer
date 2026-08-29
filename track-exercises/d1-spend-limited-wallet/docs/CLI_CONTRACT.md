# Wallet CLI contract

Status: frozen core-adapter contract for `scripts/wallet_cli.py`, schema version
`1`. This commit is a reusable integration base, not the final settlement demo
adapter. The final release path is `Settlement.Mandate:Charge`, which must
nested-exercise `DemoAsset:Pay`; the settlement owner will layer that adapter
over this immutable base and rerun the live proof.

The CLI is a small Python standard-library client for the Canton JSON Ledger
API v2. It is deliberately a transport and orchestration layer. The Daml model,
not Python, enforces the spending cap, expiry, positive-amount rule,
counterparty allow-list, and revocation.

## Invocation

Run commands from the `d1-spend-limited-wallet` project directory:

```sh
python3 scripts/wallet_cli.py health
python3 scripts/wallet_cli.py upload-dar
python3 scripts/wallet_cli.py list-parties
python3 scripts/wallet_cli.py setup-demo
python3 scripts/wallet_cli.py create-mandate
python3 scripts/wallet_cli.py status
python3 scripts/wallet_cli.py charge --counterparty Merchant-A --amount 30 --memo "approved demo charge"
python3 scripts/wallet_cli.py revoke
python3 scripts/wallet_cli.py audit
python3 scripts/wallet_cli.py run-demo
```

`--counterparty` accepts either one of the saved demo hints or a full local
party identifier. Decimal amounts are sent to the ledger as strings so that
Python binary floating-point cannot change them.

## Process contract

Every canonical command invocation and every handled command failure writes
exactly one UTF-8 JSON object followed by a newline to standard output. The
argument parser's built-in `--help` display is informational text outside the
ten-command machine contract.

- Exit `0` means the command completed and the top-level `ok` value is `true`.
- Exit `1` means a usage, configuration, transport, authentication,
  authorization, package, or ledger failure was converted to a JSON error
  envelope and `ok` is `false`.
- A Python interpreter failure before the program starts is outside this
  contract.
- Normal machine-readable output never contains a bearer token, client secret,
  complete request headers, `.env` contents, or an unsanitized HTTP response
  body.

The mode is `DEVNET` when `C8_IDP` is set to a non-empty value. It is `SANDBOX`
when `C8_IDP` is absent or empty. The commands and JSON envelope do not change
between modes.

### Success envelope

```json
{
  "schemaVersion": 1,
  "ok": true,
  "command": "health",
  "mode": "SANDBOX",
  "result": {}
}
```

The exact contents of `result` depend on the command, but the five top-level
fields above are stable. A command never includes an `error` member when
`ok` is `true`.

### Error envelope

```json
{
  "schemaVersion": 1,
  "ok": false,
  "command": "charge",
  "mode": "DEVNET",
  "error": {
    "category": "DAML_INTERPRETATION_ERROR",
    "message": "The ledger rejected the request.",
    "httpStatus": 400,
    "ledgerCode": "DAML_INTERPRETATION_ERROR",
    "errorCategory": 10,
    "grpcCodeValue": 3,
    "definiteAnswer": true
  }
}
```

`category` is a sanitized machine classification. For a Ledger API rejection,
the participant's symbolic `ledgerCode` is promoted to `category` when one is
present; otherwise the fallback is `LEDGER_REJECTED`. `message` is always
sanitized. The remaining error members are included only when the Ledger API
supplied them and they are safe to retain:

| Field | Meaning |
|---|---|
| `httpStatus` | HTTP status returned by the target. |
| `ledgerCode` | Ledger/gRPC symbolic status, if present. |
| `errorCategory` | Raw Ledger API error category, preserving its JSON type. |
| `grpcCodeValue` | Raw numeric gRPC status, if present. |
| `definiteAnswer` | Ledger-provided definite-answer flag, if present. |
| `assertion` | A recognized, sanitized Daml assertion label; never a raw stack trace. |

The CLI's own representative categories are:

| `category` | Meaning |
|---|---|
| `USAGE_ERROR` | Missing or malformed CLI arguments. |
| `AUTH_CONFIGURATION`, `AUTH_RESPONSE` | Required authentication configuration or token response is invalid. |
| `AUTH_UNREACHABLE`, `LEDGER_UNREACHABLE` | Identity provider or Ledger API cannot be reached. |
| `AUTHENTICATION` | HTTP 401: the token is missing, expired, or invalid. |
| `AUTHORIZATION` | HTTP 403: the token identity lacks access or party rights. |
| `NON_LOCAL_PARTY`, `LOCAL_PARTY_NOT_FOUND`, `AMBIGUOUS_LOCAL_PARTY` | A requested submitter is not a usable, uniquely resolved local party. |
| `CAN_ACT_AS_MISSING` | The configured ledger user does not have `CanActAs`. |
| `MANIFEST_NOT_FOUND`, `MANIFEST_INVALID` | `daml.yaml` is missing or does not expose one package name/version. |
| `DAR_NOT_FOUND`, `DAR_INSPECTION_FAILED`, `DAR_METADATA_MISMATCH`, `DPM_NOT_FOUND` | The exact current business DAR cannot be located, inspected, or matched to `daml.yaml`. |
| `SYNCHRONIZER_RESPONSE`, `SYNCHRONIZER_SELECTION_REQUIRED` | A safe package-upload target cannot be derived unambiguously. |
| `PACKAGE_VERSION_COLLISION` | The same package name/version maps to different code. |
| `PACKAGE_NOT_REGISTERED`, `PACKAGE_UPLOAD_UNVERIFIED` | Participant package registration/upload did not verify. |
| `*_NOT_FOUND`, `DEMO_NOT_SETUP` | Required saved or active demo state cannot be located. |
| `INVALID_AMOUNT` | The amount is not a finite decimal string and cannot be encoded. |
| `ATTACK_UNEXPECTEDLY_SUCCEEDED`, `DEMO_ASSERTION` | `run-demo` observed an unexpected commit or final state. |
| `ATTACK_INFRASTRUCTURE_FAILURE` | An attack step received authentication or authorization failure, not business-rule evidence. |
| `ATTACK_REJECTION_MISMATCH` | The ledger rejected an attack for a reason other than its required scenario. |
| `INTERRUPTED`, `INTERNAL_ERROR` | A sanitized interruption or unexpected internal failure. |

HTTP 401 is always `AUTHENTICATION`. HTTP 403 is always `AUTHORIZATION` at the
transport boundary; commands that can identify a missing right more precisely
use `CAN_ACT_AS_MISSING`. For other HTTP errors, the raw symbolic `ledgerCode`,
when present, becomes the top-level error `category` and is also retained in
`ledgerCode`. `errorCategory` and `grpcCodeValue` preserve the participant's
other raw category evidence. The CLI does not invent a raw category when the
participant did not return one.

## Frozen core-base Daml identifiers

The Daml owner confirmed this integration interface for the core branch:

| Item | Confirmed value |
|---|---|
| Module | `Mandate` |
| Proposal template | `MandateProposal` |
| Active mandate template | `Mandate` |
| Audit template | `ChargeRecord` |
| Proposal acceptance choice | `Accept` |
| Charge choice | `Charge` |
| Revocation choice | `Revoke` |

Fully qualified identifiers use the main package ID extracted from the exact
DAR being uploaded, for example
`<package-id>:Mandate:MandateProposal`. A package ID is never copied from an
old build or hard-coded.

`MandateProposal` has these fields:

```text
owner
agent
cap
expiresAt
allowedCounterparties
mandateReference
```

`Accept` returns `ContractId Mandate`. The `Charge` arguments are:

```text
counterparty
amount
memo
```

`Charge` returns a `ChargeResult` with `mandateCid` and `auditCid`.
`ChargeRecord` contains:

```text
owner
agent
counterparty
amount
transactionTime
memo
mandateReference
previousSpent
newSpent
cap
remainingAllowance
expiresAt
allowedCounterpartiesAtCharge
```

These identifiers are confirmed for the core compatibility base. They are
intentionally isolated in constants near the start of `wallet_cli.py` so the
settlement overlay can replace the module/template/result decoding without
changing the canonical command-line interface. They are not the final release
value path, and no package hash from this core-only proof may be pinned by the
settlement adapter.

## Command results and behavior

The tables below describe the required behavior and minimum result evidence.
Additional non-secret diagnostic fields may be added without changing schema
version `1`.

### `health`

Runs the relevant equivalent of upstream `python3 c8lab.py check`:

1. Classifies the target as `DEVNET` or `SANDBOX`.
2. Reports each required environment variable only as `SET` or `UNSET`.
3. Obtains an access token in DevNet mode without exposing it.
4. calls `GET /v2/state/ledger-end`.
5. calls `GET /v2/parties` and counts only entries where `isLocal` is `true`.

Minimum `result` evidence:

```json
{
  "environment": {
    "C8_BASE": "SET",
    "C8_IDP": "UNSET",
    "C8_CLIENT_ID": "UNSET",
    "C8_CLIENT_SECRET": "UNSET",
    "C8_REGISTRY": "UNSET"
  },
  "checks": {
    "authentication": "ok",
    "ledgerEnd": "...",
    "localPartyCount": 4,
    "canActAsLocalPartyCount": 4
  }
}
```

`health` proves reachability and basic authentication. It does not prove
`CanActAs` for every required demo party; `setup-demo` does that after selecting
the exact parties.

### `upload-dar`

The command requires the exact `.daml/dist/<name>-<version>.dar` derived from
`name` and `version` in `daml.yaml`, then runs
`dpm damlc inspect-dar <dar> --json` and reads `main_package_id` plus the main
package's name/version. Inspection must report the same name/version as the
manifest. A differently named sole DAR is never accepted as a fallback. A
`WALLET_DAR` override may point to a build in another isolated worktree, but
its inspected main package name/version must still match the current manifest.
The Python implementation has no third-party library dependency; the Daml
toolchain remains required for DAR inspection.

Minimum `result` evidence:

```json
{
  "path": ".daml/dist/<name>-<version>.dar",
  "packageName": "d1-spend-limited-wallet",
  "packageVersion": "0.0.1",
  "packageId": "<64-hex-main-package-id>",
  "uploadResult": "uploaded",
  "synchronizerSelection": "connected_synchronizers",
  "packageStatus": "PACKAGE_STATUS_REGISTERED"
}
```

`uploadResult` is `uploaded` after a successful raw binary
`POST /v2/packages`, or `already_present` when `GET /v2/packages` already
contains the exact main package ID. Both are successful, idempotent outcomes,
and both require `PACKAGE_STATUS_REGISTERED` from the participant.
`synchronizerSelection` is present for `uploaded`; `already_present` performs
no upload-target selection and therefore omits that member.

Before uploading an absent package ID, the client asks the participant's
package-vetting metadata for a different ID with the same package name/version.
That endpoint is collision metadata only; it is never used to discover where
the participant is connected. If a collision exists, the client stops with
`PACKAGE_VERSION_COLLISION`. If the metadata API is unavailable, the
participant's upload decision is still authoritative. A participant
`KNOWN_PACKAGE_VERSION` or `KNOWN_DAR_VERSION` response is converted to
`PACKAGE_VERSION_COLLISION` while retaining the safe raw fields. The client
never retries different code under the same name/version and never edits
`daml.yaml`.

For a new upload, an explicit non-empty `C8_SYNCHRONIZER_ID` wins. Otherwise
the client calls `GET /v2/state/connected-synchronizers` with no party filter,
retries an empty list for a short bounded interval, and requires exactly one
non-empty `synchronizerId`. Zero or multiple connected IDs fail closed before
the DAR payload is loaded for upload or posted. The request is always
explicitly qualified as
`POST /v2/packages?synchronizerId=<URL-encoded-in-memory-ID>&vetAllPackages=true`;
there is no unqualified-upload fallback. The identifier is retained in memory
only. Result JSON exposes `synchronizerSelection` as either `configured` or
`connected_synchronizers`, never the identifier itself.

### `list-parties`

Returns only participant-hosted parties (`isLocal: true`). Rights are verified
by `health` in aggregate and by `setup-demo` for each selected demo party.

```json
{
  "count": 1,
  "parties": [
    {
      "party": "Owner::<participant-namespace>",
      "hint": "Owner",
      "isLocal": true
    }
  ]
}
```

Non-local parties must not be returned as candidates for submission.

### `setup-demo`

Finds or allocates the four demo parties by hint: `Owner`, `Agent`,
`Merchant-A`, and `Merchant-B`. It searches local parties first and never
allocates a duplicate merely because the command has run before. If multiple
local parties share a hint, resolution fails with `AMBIGUOUS_LOCAL_PARTY`;
an operator may retry with one exact full party ID where the command accepts a
party argument. Before saving the setup, it verifies `CanActAs` for every
party that the CLI will submit as.

The result contains a `parties` object keyed by `owner`, `agent`, `merchantA`,
and `merchantB`, a `partyActions` object whose values are `reused` or
`allocated`, and a `canActAs` object whose values must all be `true`.

### `create-mandate`

Creates the proposal as Owner and accepts it as Agent. The confirmed proposal
has cap `100.0`, only Merchant-A in `allowedCounterparties`, a future expiry,
and a unique `mandateReference`. The result includes the proposal contract ID,
active mandate contract ID, mandate reference, cap, expiry, and allowed party.

This is ledger submission, not local simulation. The client verifies
`CanActAs Owner` before proposal creation and `CanActAs Agent` before
acceptance.

### `status`

Queries the active contract set at a captured ledger end and reports the latest
mandate for the saved reference. `result.activeMandate` contains the contract
ID, `spent`, `cap`, `expiresAt`, allowed counterparties, and calculated
`remainingAllowance` when an active contract exists; otherwise it is `null`.
`result.revoked` is `true` only when saved revocation evidence exists and the
mandate is no longer active.

### `charge`

Submits `Charge` as Agent using exactly the requested counterparty, decimal
amount, and memo. On success, the core-base result includes the
update/transaction ID, replacement mandate contract ID, and audit contract ID.
Use `status` to query the replacement mandate's ledger-reported spend.

The CLI may validate JSON/argument shape, resolve a local party hint, and
verify `CanActAs Agent`. It must not independently reject an amount because it
would exceed the cap, reject a counterparty because it is absent from the
allow-list, reject because the mandate appears expired, or reject because an
earlier mandate contract was revoked. Those are Daml decisions. This rule is
what makes the attack steps in `run-demo` meaningful.

### `revoke`

Exercises `Revoke` as Owner against the current active mandate. The result
includes the committed update/transaction ID and archived mandate contract ID.
The client verifies `CanActAs Owner` before submission.

### `audit`

Queries `ChargeRecord` contracts visible to the saved parties and filters them
to the saved `mandateReference`. It returns ledger-derived records ordered by
`transactionTime`. It does not synthesize records from attempted charges or
the local state file.

### `run-demo` (core-base proof)

Runs the hardened core-interface security sequence. It is a base integration
proof, not final release evidence after the settlement scope correction:

1. Uploads or verifies the exact business DAR.
2. Identifies or reuses four local parties and verifies submission rights.
3. Creates and accepts a mandate with cap `100.0` and only Merchant-A allowed.
4. Charges Merchant-A `30.0` and requires a committed update/transaction ID.
5. Queries the replacement mandate and its `ChargeRecord`.
6. Submits Merchant-A `80.0` and requires a ledger rejection.
7. Submits Merchant-B `10.0` and requires a ledger rejection.
8. Revokes as Owner and requires a committed update/transaction ID.
9. Submits Merchant-A `1.0` against the revoked mandate and requires a ledger
   rejection.
10. Queries the final audit and proves that exactly one charge exists, for
    Merchant-A and `30.0`.

Expected ledger rejections are successful demo observations, not top-level CLI
failures. For each attack, `result.deliberateRejections` contains `attack`,
`rejected: true`, and a `rawCategory` object. That object retains only the
participant's safe `httpStatus`, `ledgerCode`, `errorCategory`,
`grpcCodeValue`, `definiteAnswer`, and recognized `assertion` fields actually
returned. If an attack commits, or if the final audit differs, `run-demo` fails
with `ATTACK_UNEXPECTEDLY_SUCCEEDED` or `DEMO_ASSERTION`.

A rejection counts only when it proves the intended scenario:

| Attack | Required sanitized ledger evidence |
|---|---|
| `cap_exceeded` | `ledgerCode` is `DAML_FAILURE` and `assertion` is exactly `charge would exceed the cap`. |
| `counterparty_not_allowed` | `ledgerCode` is `DAML_FAILURE` and `assertion` is exactly `counterparty is not allow-listed`. |
| `revoked_mandate` | `ledgerCode` is exactly `CONTRACT_NOT_FOUND`, `CONTRACT_NOT_ACTIVE`, or `CONTRACT_NOT_FOUND_OR_NOT_ACTIVE`. |

The matcher fails closed. HTTP 401/403 or ledger codes `UNAUTHENTICATED` and
`PERMISSION_DENIED` produce `ATTACK_INFRASTRUCTURE_FAILURE`; they never count
as cap, allow-list, or revocation proof. Any unrelated assertion, malformed
error, or other ledger code produces `ATTACK_REJECTION_MISMATCH`. This prevents
a broken token, missing right, wrong choice shape, or unrelated contract error
from making the security demo appear to pass.

## Local state

The default state file is `.wallet_cli_state.json` in the project root. Set
`WALLET_CLI_STATE` to override its path. The file may contain package IDs,
party IDs, contract IDs, command/update IDs, the mandate reference, target
mode, and a one-way SHA-256 target fingerprint used to prevent cross-ledger
contract-ID reuse. It must never contain the raw target URL, credentials,
tokens, authorization headers, cookies, or raw error bodies.

The state file is generated evidence, not source code, and must remain
untracked. Deleting it does not change ledger state; commands must still query
the ledger rather than treating the file as authoritative. If the saved
`mandateReference` is missing, mutating commands fail closed instead of
selecting an unrelated contract owned by a reused party.

## Compatibility rules

- Only the package ID derived from the current DAR may prefix template IDs.
- Only parties with `isLocal: true` are eligible for submission.
- Every submitter must have a verified `CanActAs` right before a command is
  sent.
- A 401 is an authentication failure. A 403 is an authorization failure and
  commonly means missing `CanActAs`.
- The CLI uses the authenticated JSON Ledger API below `C8_BASE`.
- The DAR body is sent as raw bytes to `POST /v2/packages`; it is not JSON,
  base64, or multipart form data.
- There is no `dpm ledger upload-dar` command. Build, test, and participant
  upload are separate gates.
- `C8_REGISTRY` is reported for environment completeness but is not used by
  this core-base demonstration. The settlement overlay's `DEMO-GBP` is an
  issuer-backed demo ledger asset and must never be described as Canton Coin.
- The CLI requires only Python's standard library and does not require Docker.
