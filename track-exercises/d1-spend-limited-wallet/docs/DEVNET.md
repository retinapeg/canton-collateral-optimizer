# DevNet and sandbox operations

This guide runs the spend-limited wallet against either the shared Cantor8
DevNet participant or a local `dpm sandbox` JSON API. The CLI interface and
output contract are identical in both modes.

This branch freezes the hardened core CLI producer. It is not final release
evidence: the authoritative final value path is
`Settlement.Mandate:Charge`, nested-exercising `DemoAsset:Pay`. The settlement
overlay must preserve these command names and safety checks, derive its
combined DAR/package ID dynamically, and produce fresh proof after adaptation.

## Verified local toolchain

The baseline was built and tested with:

```text
DPM executable: ~/.dpm/bin/dpm
DPM: 1.0.21
Daml SDK: 3.5.7
Canton sandbox: 3.5.14
Java: OpenJDK 17
```

`daml.yaml` pins SDK `3.5.7`. A plain shell may not include the verified DPM
and Java installations even though VS Code does. If `dpm` or Java cannot be
found, establish the same local toolchain paths without changing the project:

```sh
export JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
export PATH="$HOME/.dpm/bin:/opt/homebrew/opt/openjdk@17/bin:$PATH"
```

No Docker daemon or image is needed for this workflow.

## Known reference versions

The upstream reference is Cantor8's
[`hackathon-toolkit`](https://github.com/Cantor8/hackathon-toolkit). A fresh
remote check on 2026-08-29 resolved `main` and `HEAD` to:

```text
4e836376654ae97c8cb86e149dc5b1a39bc549e7
```

The earlier local reference snapshot was:

```text
c5b1779
```

That local snapshot is stale. The following fresh, commit-pinned files were
inspected instead:

- [`c8lab.py`](https://github.com/Cantor8/hackathon-toolkit/blob/4e836376654ae97c8cb86e149dc5b1a39bc549e7/c8lab.py)
- [`API.md`](https://github.com/Cantor8/hackathon-toolkit/blob/4e836376654ae97c8cb86e149dc5b1a39bc549e7/API.md)
- [`SETUP.md`](https://github.com/Cantor8/hackathon-toolkit/blob/4e836376654ae97c8cb86e149dc5b1a39bc549e7/SETUP.md)
- [`TROUBLESHOOTING.md`](https://github.com/Cantor8/hackathon-toolkit/blob/4e836376654ae97c8cb86e149dc5b1a39bc549e7/TROUBLESHOOTING.md)

The CLI follows the useful upstream patterns for client-credentials
authentication, local-party filtering, hint reuse, and explicit `CanActAs`
verification. It intentionally differs in three places: it emits the JSON
contract documented in [CLI_CONTRACT.md](CLI_CONTRACT.md), owns the business
DAR upload flow, and uses the no-Docker DPM sandbox rather than the toolkit's
Docker LocalNet when DevNet is unavailable.

## The three gates

These are independent checks. Passing one does not imply either of the others.

### Gate 1: build the DAR

From this project directory:

```sh
dpm build
```

This compiles the Daml project and creates a DAR under `.daml/dist/`. The CLI
reads `name` and `version` from `daml.yaml`, requires exactly the resulting
`.daml/dist/<name>-<version>.dar`, and runs
`dpm damlc inspect-dar <dar> --json` to derive `main_package_id` plus the main
package name/version. It rejects a missing exact artifact and rejects any DAR
whose inspected main package name/version differs from `daml.yaml`; a stale
sole DAR in `dist/` is never a fallback.

`dpm build` does not connect to a participant and does not upload anything.
For additional artifact integrity evidence, the verified toolchain also
supports:

```sh
dpm validate-dar .daml/dist/<name>-<version>.dar
```

Re-derive the filename from the current `daml.yaml` after a package-version
change rather than copying this baseline path.

### Gate 2: test the Daml model

```sh
dpm test
```

This runs the Daml Script tests against the IDE/test ledger. It proves the
model behavior covered by those tests. It does not prove DevNet
authentication, party rights, JSON command shapes, or package upload.

### Gate 3: upload to the running participant

```sh
python3 scripts/wallet_cli.py upload-dar
```

This is the network gate. The command authenticates as required and sends the
exact DAR bytes to the explicitly qualified package endpoint:

```text
POST ${C8_BASE}/v2/packages?synchronizerId=<URL-encoded-in-memory-ID>&vetAllPackages=true
```

with:

```text
Authorization: Bearer <redacted-token>   # DevNet only
Content-Type: application/octet-stream
```

The request body is the raw binary DAR. It is not a JSON string, base64 value,
or multipart upload.

There is no `dpm ledger upload-dar` command. Do not spend time looking for one.
Modern DPM intentionally leaves participant upload to the JSON/gRPC APIs,
Canton console, or declarative configuration. This integration uses the JSON
package endpoint required by the target participant. Digital Asset's
[DPM migration reference](https://docs.digitalasset.com/build/3.4/dpm/dpm.html#removed-command-replacements)
documents the removed command family and API replacements.

## Configuration without credential leakage

Copy `.env.example` to a local `.env`, replace every placeholder locally, and
ensure `.env` remains ignored. The CLI and wrapper must never print the file or
its values.

| Variable | DevNet | Sandbox | Safe reporting rule |
|---|---|---|---|
| `C8_BASE` | Required Ledger API base | `http://localhost:7575` | `SET`/`UNSET` only |
| `C8_IDP` | Required Keycloak base | Unset | `SET`/`UNSET` only |
| `C8_CLIENT_ID` | Required OAuth client ID | Unset | `SET`/`UNSET` only |
| `C8_CLIENT_SECRET` | Required OAuth secret | Unset | `SET`/`UNSET` only |
| `C8_REGISTRY` | Set if supplied by Cantor8 | Unset | `SET`/`UNSET` only |
| `C8_SYNCHRONIZER_ID` | Optional explicit connected target | Optional | Never emit the value |

`C8_REGISTRY` is part of the upstream environment check. This core-base demo
records mandate charges and makes no registry request. The settlement overlay
uses `DEMO-GBP`, an issuer-backed demo ledger asset; it must never label that
asset Canton Coin.

For DevNet, the CLI obtains a token using the OAuth client-credentials request
to the configured identity provider. The form body, secret, access token, and
`Authorization` header must remain in memory only. They must never be written
to `.wallet_cli_state.json`, a transcript, a traceback, or a committed file.

Run the safe preflight:

```sh
python3 scripts/wallet_cli.py health
```

The `environment` member reports only `SET` or `UNSET` for the five required
upstream variables. `C8_SYNCHRONIZER_ID` is an optional upload override and is
never serialized, even as part of that status map.

## Authentication and rights

Authentication and party authority are separate.

- HTTP 401 means the target did not accept the access token. Check the identity
  provider, client ID, client secret, token expiry, and Ledger API base without
  printing any of them.
- HTTP 403 means the token is valid enough to reach an authorization decision,
  but the user is not allowed to perform the operation. For ledger submission,
  a missing `CanActAs` right is the common cause.
- A token does not acquire party rights merely by naming a party in `actAs`.

Before any submission, the CLI reads the current user's rights and verifies a
`CanActAs` entry for each member of `actAs`. Setup may request a grant only
through the supported user-rights endpoint and only when the token is
authorized to do so. It then reads the rights again; it never assumes a grant
succeeded based only on the POST response.

Handled errors expose only the sanitized fields described in
[CLI_CONTRACT.md](CLI_CONTRACT.md). In particular, the CLI does not include the
raw token endpoint body, Ledger API response body, request headers, URL query
parameters, or stack trace in output.

## Party selection

The participant can list parties learned from other nodes. Those parties are
not necessarily hosted by this participant.

1. Call `GET /v2/parties`.
2. Discard every party unless `isLocal` is exactly `true`.
3. Match an existing local party by its hint before attempting allocation.
4. If more than one local party has that hint, fail with
   `AMBIGUOUS_LOCAL_PARTY`; never pick the lexicographically first match.
5. Allocate only a genuinely missing hint.
6. Verify `CanActAs` for every party used in `actAs`.

The demo hints are:

```text
Owner
Agent
Merchant-A
Merchant-B
```

Re-running setup must reuse those local parties. Duplicate allocation is not a
recovery strategy. `NO_SYNCHRONIZER_ON_WHICH_ALL_SUBMITTERS_CAN_SUBMIT` usually
means a non-local party was selected; stop and inspect `list-parties` rather
than retrying blindly.

## Idempotent and collision-safe package upload

Package identity is the main package hash, not just the friendly name and
version.

The upload sequence is:

1. Derive the exact `<name>-<version>.dar` path from the current `daml.yaml`;
   do not fall back to another sole artifact in `dist/`.
2. Inspect that exact DAR, require its main package name/version to match the
   manifest, and derive its main package ID.
3. Read the participant's package IDs.
4. If the exact package ID exists and is registered, return `already_present`
   and do not POST.
5. Ask participant package-vetting metadata whether a different package ID
   already has the same package name/version. If so, stop with
   `PACKAGE_VERSION_COLLISION`.
6. If that optional metadata surface is unavailable, retain that fact in the
   result and allow the participant's upload collision check to decide.
7. Use an explicit non-empty `C8_SYNCHRONIZER_ID` if configured. Otherwise call
   `GET /v2/state/connected-synchronizers` with no party filter. Retry an empty
   list only for a short bounded interval.
8. Require exactly one non-empty connected ID. Zero after the retry or multiple
   IDs is `SYNCHRONIZER_SELECTION_REQUIRED`; do not load the DAR payload for
   upload or POST it.
9. Retain the selected ID in memory only and POST the raw DAR bytes with both
   `synchronizerId=<URL-encoded-ID>` and `vetAllPackages=true`. Never attempt a
   bare package POST. Package-vetting metadata is for collision detection, not
   connectivity discovery.
10. Re-read package status/listing and require the exact package ID to be
   `PACKAGE_STATUS_REGISTERED` before recording `uploaded`.

Digital Asset's
[`GET /v2/state/connected-synchronizers` OpenAPI](https://docs.digitalasset.com/build/3.5/reference/json-api/openapi.html)
defines the participant connection-state route, and the official
[external-party onboarding quickstart](https://docs.digitalasset.com/build/3.5/quickstart/operate/how-to-onboard-external-parties-in-quickstart.html)
uses it to derive a synchronizer ID. Package-vetting remains a separate
topology/metadata surface.

The participant may reject different rebuilt code that reuses a known package
name/version. A `KNOWN_PACKAGE_VERSION` response is terminal for that artifact:
do not retry it, do not claim the upload succeeded, and do not work around it
by deleting local state.

The smallest safe recovery is for the Daml owner to increment only the patch
component in `daml.yaml` (for example, `0.0.1` to `0.0.2`), then rerun both
`dpm build` and `dpm test`. The integration owner then derives the new DAR and
package ID and uploads that artifact. The CLI must never modify the Daml
package version automatically.

## DevNet runbook

From the project directory:

1. Export the five `C8_*` values in the current shell or load them from a local,
   ignored `.env` without displaying it.
2. Run `python3 scripts/wallet_cli.py health`.
3. Confirm `result.checks.authentication` is `ok` and review only the
   `SET`/`UNSET` map.
4. Run `python3 scripts/wallet_cli.py list-parties` and confirm every selected
   party is local.
5. Run `python3 scripts/wallet_cli.py setup-demo` and require every value in
   `result.canActAs` to be `true`.
6. Run the complete demo with one command:

```sh
./scripts/demo_devnet.sh
```

The wrapper executes the same `wallet_cli.py run-demo` contract. It must not
enable shell tracing because tracing would disclose exported secrets. A
successful result contains the exact package ID, local party IDs, committed
30-unit transaction/update ID, all three raw ledger rejection categories, the
revocation transaction/update ID, and final one-record audit proof.

Do not redirect an unsanitized debug stream. The JSON emitted by the CLI is the
only supported transcript source.

## Sandbox fallback without Docker

If DevNet is unavailable, start the SDK sandbox with its JSON API in one
terminal:

```sh
dpm sandbox --json-api-port 7575
```

The `--json-api-port` behavior is documented in Digital Asset's
[sandbox reference](https://docs.digitalasset.com/build/3.4/component-howtos/application-development/dpm-sandbox.html).

Do not pass `--dar` here: keeping package upload as Gate 3 proves the same
`POST /v2/packages` integration used on DevNet.

In a second terminal, from the project directory:

```sh
export C8_BASE=http://localhost:7575
unset C8_IDP C8_CLIENT_ID C8_CLIENT_SECRET C8_REGISTRY C8_SYNCHRONIZER_ID
./scripts/demo_devnet.sh
```

The default sandbox accepts valid Ledger API requests without authorization;
the CLI still follows the same local-party and rights checks, and its mode is
`SANDBOX`. No Docker image or Docker daemon is required. The CLI command names,
state file, package upload path, attack submissions, and JSON output envelope
remain unchanged.

A sandbox run is strong integration evidence, but it is not DevNet evidence.
Report the target mode truthfully.

## What `run-demo` proves

The demo must observe this ledger sequence, in order:

| Step | Required observation |
|---|---|
| Package | Current business package ID is present on the participant. |
| Setup | Owner, Agent, Merchant-A, and Merchant-B are local and reusable. |
| Mandate | Cap is `100.0`; only Merchant-A is allowed; Agent accepts. |
| Charge 30 | Merchant-A `30.0` commits and returns an update/transaction ID. |
| Query | Replacement mandate has spent `30.0`; one matching audit exists. |
| Attack 80 | Command is submitted; code is `DAML_FAILURE` and exact assertion is `charge would exceed the cap`. |
| Attack B | Command is submitted; code is `DAML_FAILURE` and exact assertion is `counterparty is not allow-listed`. |
| Revoke | Owner's revocation commits. |
| Attack 1 | Command is submitted after revocation; code is an allowed stale-contract code. |
| Final audit | Exactly one charge exists: Merchant-A `30.0`. |

The Python client must not duplicate the cap, expiry, allow-list, or revocation
checks. An attack rejected locally does not prove ledger enforcement. An attack
that commits is a demo failure even if Python would have preferred to reject
it.

The accepted post-revocation code set is deliberately narrow:

```text
CONTRACT_NOT_FOUND
CONTRACT_NOT_ACTIVE
CONTRACT_NOT_FOUND_OR_NOT_ACTIVE
```

HTTP 401/403 and ledger codes `UNAUTHENTICATED`/`PERMISSION_DENIED` mean the
attack was not authorized to reach the model. `run-demo` reports
`ATTACK_INFRASTRUCTURE_FAILURE`, not success. Any other rejection that fails
the exact scenario match is `ATTACK_REJECTION_MISMATCH`.

## Pre-final core-base sandbox evidence

The sanitized [DevNet/sandbox run transcript](DEVNET_RUN_TRANSCRIPT.json)
records a 2026-08-29 fallback run against DPM sandbox. It is retained only as
transport/core-policy evidence and is explicitly not final release proof. No
pre-final package hash is pinned: the settlement overlay must inspect the
post-merge DAR and discover its package ID at runtime. Proposal, acceptance,
the 30-unit charge, and revocation returned committed updates, and the final
core-base audit count was one. Exact ephemeral party, contract, update, local
worktree, and generated-artifact paths are intentionally omitted. The observed
core-base rejection evidence was:

| Scenario | HTTP | `ledgerCode` | `errorCategory` | `grpcCodeValue` | Assertion |
|---|---:|---|---:|---:|---|
| Cap | 400 | `DAML_FAILURE` | 9 | 9 | `charge would exceed the cap` |
| Allow-list | 400 | `DAML_FAILURE` | 9 | 9 | `counterparty is not allow-listed` |
| Post-revoke | 404 | `CONTRACT_NOT_FOUND` | 11 | 5 | Not supplied |

This proves the no-Docker transport and strict rejection machinery against the
core interface. It does not prove the final settlement path, asset balances,
DevNet authentication, or DevNet party rights; all five DevNet variables were
unset.

## Failure triage

| Observation | Category | Action |
|---|---|---|
| No TCP/HTTP response | `LEDGER_UNREACHABLE` | Check target availability and `C8_BASE` without printing its value. |
| HTTP 401 | `AUTHENTICATION` | Refresh/fix credentials; never paste the token into output. |
| HTTP 403 | `AUTHORIZATION` | Inspect user rights and selected party; verify `CanActAs`. |
| Party has `isLocal: false` | `NON_LOCAL_PARTY` | Select/reuse a local hint; do not submit. |
| Party hint already exists | Not an error | Reuse the local party. |
| `KNOWN_PACKAGE_VERSION` | `PACKAGE_VERSION_COLLISION` | Ask the Daml owner for the smallest patch-version bump, rebuild, and retest. |
| Expected attack commits | `ATTACK_UNEXPECTEDLY_SUCCEEDED` | Preserve sanitized evidence and escalate to the Daml owner. |
| Attack receives 401/403 | `ATTACK_INFRASTRUCTURE_FAILURE` | Repair authentication/rights; do not count it as model enforcement. |
| Attack rejection has wrong assertion/code | `ATTACK_REJECTION_MISMATCH` | Inspect command shape and model/package identity; fail closed. |
| Final audit has more than one charge | `DEMO_ASSERTION` | Stop; do not describe the demo as successful. |

## Evidence checklist

A release evidence bundle must contain:

- branch and commit SHA;
- DAR path and main package ID derived from the final combined build and
  reported outside committed artifacts;
- target mode, exactly `DEVNET` or `SANDBOX`;
- the four party role labels, all proven local; exact ephemeral IDs are handed
  off outside committed artifacts;
- package upload result, `uploaded` or `already_present`;
- `COMMITTED` evidence for the 30-unit transaction; its exact ephemeral update
  ID is handed off outside committed artifacts;
- raw ledger categories for cap, allow-list, and post-revocation rejections;
- final audit count and the one successful charge;
- the exact one-command invocation;
- any unresolved blocker.

The run transcript contains the runtime fields. The final branch and commit
SHA may be reported alongside it after the immutable commit is created; the
transcript must not pretend to know the SHA of a commit that does not yet
exist.

It must not contain access tokens, client secrets, `.env` values, request
headers, cookies, token endpoint payloads, or unsanitized Ledger API bodies.
