# PRE-SETTLEMENT/NON-FINAL DevNet integration handoff

Last updated: 2026-08-29

> **Non-final base:** this handoff freezes the reusable core CLI producer only.
> The release demo must be adapted to `Settlement.Mandate:Charge`, which
> nested-exercises `DemoAsset:Pay`, and must query `Settlement:Mandate`,
> `Settlement:DemoAsset`, and `Settlement:ChargeRecord`. No core-only package
> hash or transcript in this branch is final release evidence.

> **Fresh-upload correction:** the earlier `60d57f3` / `4f2f860` handoff
> overstated the reliability of a first upload to an empty participant.
> Package-vetting metadata can contain no matching `vettedPackages`, and an
> unqualified `POST /v2/packages` can then fail with
> `PACKAGE_SERVICE_CANNOT_AUTODETECT_SYNCHRONIZER`. The additive hardening
> follow-up containing this handoff supersedes that claim: it discovers the
> connection through `GET /v2/state/connected-synchronizers`, requires exactly
> one non-empty ID (or an explicit `C8_SYNCHRONIZER_ID`), and always sends an
> explicitly qualified package upload without serializing the ID.

## Scope and branch

- Worktree: `.worktrees/agent-devnet` inside the authoritative repository
  folder, isolated from the main checkout
- Branch: `agent/devnet`
- Frozen producer commit: `60d57f3eb82302c979645b25c6d17ede94834145`
- Producer parent: `1f8aba1a2dc68f60b5e74fbca8b887e225927103`
- Integration-owned paths: `scripts/`, `.env.example`, relevant `.gitignore`
  entries, and `docs/DEVNET*.md` / `docs/CLI_CONTRACT.md`
- Daml and demo paths remain read-only to this integration branch unless an
  integration-blocking defect is demonstrated.

The original main worktree's pre-existing blank-line change in
`daml/Mandate.daml` was not copied, overwritten, reset, or otherwise modified.
`docs/CURRENT_STATE.md` and the `docs/` directory were absent at the starting
commit, so there was no current-state document to inspect; the initial state
was reconstructed from the committed Daml interface and verified toolchain
evidence.

The frozen producer commit contains exactly these eight files:

```text
track-exercises/d1-spend-limited-wallet/.env.example
track-exercises/d1-spend-limited-wallet/.gitignore
track-exercises/d1-spend-limited-wallet/docs/CLI_CONTRACT.md
track-exercises/d1-spend-limited-wallet/docs/DEVNET.md
track-exercises/d1-spend-limited-wallet/docs/DEVNET_RUN_TRANSCRIPT.json
track-exercises/d1-spend-limited-wallet/scripts/demo_devnet.sh
track-exercises/d1-spend-limited-wallet/scripts/test_wallet_cli.py
track-exercises/d1-spend-limited-wallet/scripts/wallet_cli.py
```

The schema-clarification commit `4f2f86084060d94f8c0a897758271275c14b23bd`
is a documentation-only child of the frozen producer. The current additive
hardening follow-up is a child of `4f2f86084060d94f8c0a897758271275c14b23bd`
and changes only integration-owned CLI, test, environment-example, and DevNet
documentation paths. It must be applied after the frozen producer anywhere
first-upload reliability matters; the exact immutable follow-up SHA is
reported alongside this handoff after Git creates the commit.

The additive follow-up changes exactly these seven owned files:

```text
track-exercises/d1-spend-limited-wallet/.env.example
track-exercises/d1-spend-limited-wallet/docs/CLI_CONTRACT.md
track-exercises/d1-spend-limited-wallet/docs/DEVNET.md
track-exercises/d1-spend-limited-wallet/docs/DEVNET_HANDOFF.md
track-exercises/d1-spend-limited-wallet/docs/DEVNET_RUN_TRANSCRIPT.json
track-exercises/d1-spend-limited-wallet/scripts/test_wallet_cli.py
track-exercises/d1-spend-limited-wallet/scripts/wallet_cli.py
```

## Current checkpoint

The core-interface transport run has passed against the no-Docker DPM sandbox.
After the correction above, the exact CLI flow also passed against two wholly
fresh sandbox participant processes started without a business DAR. In both
runs, the connected-synchronizer endpoint returned one non-empty target, the
ID remained in memory, the explicitly qualified raw upload was accepted, and
the package reached `PACKAGE_STATUS_REGISTERED`. Its credential-safe evidence is in
[`DEVNET_RUN_TRANSCRIPT.json`](DEVNET_RUN_TRANSCRIPT.json). All three gates
passed independently:

```text
dpm build: PASS
dpm test: PASS
POST /v2/packages: PASS
```

The pre-settlement compatibility build used Daml-core commit
`264b5cbecd1ebcdc7a60db0671c10bf140d71022`; all 19 Daml Script tests passed.
Its package hash was checked during the live run but is deliberately not pinned
here because the final combined settlement build must produce a different hash.

The tested artifact's path was derived programmatically and intentionally
omitted from committed evidence. Its now-pre-final package hash is also
omitted. The settlement overlay must build the combined source, inspect that
exact DAR, derive the package ID dynamically, upload it, and generate fresh
evidence.

The superseded pre-core artifact does not expose the allow-list/audit
interface. Its package ID is intentionally omitted and must not be reported or
uploaded as the final business package.

The verified toolchain was DPM `1.0.21`, Daml SDK `3.5.7`, Canton sandbox
`3.5.14`, and OpenJDK 17. Package status changed from
`PACKAGE_STATUS_UNSPECIFIED` to `PACKAGE_STATUS_REGISTERED` only after raw
binary `POST /v2/packages`; the identical DAR was then observed as
`already_present` without re-upload.

## Confirmed and sandbox-proven core interface

The Daml owner has confirmed the integration contract:

- module `Mandate`;
- templates `MandateProposal`, `Mandate`, and `ChargeRecord`;
- proposal fields `owner`, `agent`, `cap`, `expiresAt`,
  `allowedCounterparties`, and `mandateReference`;
- `Accept : ContractId Mandate`;
- `Charge` arguments `counterparty`, `amount`, and `memo`;
- `Charge` result `ChargeResult { mandateCid, auditCid }`;
- `ChargeRecord` fields `owner`, `agent`, `counterparty`, `amount`,
  `transactionTime`, `memo`, `mandateReference`, `previousSpent`, `newSpent`,
  `cap`, `remainingAllowance`, `expiresAt`, and
  `allowedCounterpartiesAtCharge`.

The exact interface above built, passed its Daml tests, and completed the live
sandbox JSON API base demo. It is now a compatibility input, not the final
business route. The settlement producer owns the revised fields and adapter;
unavailable DevNet credentials remain a separate blocker.

## Environment state

Checked in this worktree on 2026-08-29. Values were not read into output.

```text
C8_BASE=UNSET
C8_IDP=UNSET
C8_CLIENT_ID=UNSET
C8_CLIENT_SECRET=UNSET
C8_REGISTRY=UNSET
```

Consequences:

- no DevNet token was requested;
- no DevNet health/package/party/rights call was attempted;
- no live DevNet party was selected or allocated;
- no package was uploaded to DevNet;
- no DevNet transaction/update ID or rejection category exists.

The supported fallback target is a local `dpm sandbox --json-api-port 7575`
with `C8_BASE=http://localhost:7575` and the remaining `C8_*` variables unset.
That fallback completed successfully and is labelled `SANDBOX`, never
`DEVNET`.

## Proven core-base sandbox run (not final release proof)

The exact invocation was:

```sh
./scripts/demo_devnet.sh
```

Party roles used, all local to the sandbox participant:

```text
Owner
Agent
Merchant-A
Merchant-B
```

Exact party IDs were returned by the live sandbox and verified local, but are
intentionally omitted from committed artifacts and handed to the integrator
outside the repository.

Package evidence:

```text
fresh run-demo upload process 1: uploaded
fresh run-demo upload process 2: uploaded
selection source: connected_synchronizers
separate idempotency check: already_present
status: PACKAGE_STATUS_REGISTERED
```

Successful update evidence:

```text
proposal: COMMITTED
accept: COMMITTED
charge30: COMMITTED
revoke: COMMITTED
```

The exact ephemeral update and contract IDs were returned live but are
intentionally omitted from committed artifacts and handed to the integrator
outside the repository.

Deliberate rejection evidence:

| Attack | HTTP | `ledgerCode` | `errorCategory` | `grpcCodeValue` | Assertion |
|---|---:|---|---:|---:|---|
| Merchant-A 80 after 30 | 400 | `DAML_FAILURE` | 9 | 9 | `charge would exceed the cap` |
| Merchant-B 10 | 400 | `DAML_FAILURE` | 9 | 9 | `counterparty is not allow-listed` |
| Merchant-A 1 after revoke | 404 | `CONTRACT_NOT_FOUND` | 11 | 5 | Not supplied |

Final audit proof: exactly one record, amount `30.0000000000`, remaining
allowance before revocation `70.0000000000`, and no audit records from the
three failed charges.

## Upstream comparison

A fresh remote check resolved Cantor8 `hackathon-toolkit` `main` to:

```text
4e836376654ae97c8cb86e149dc5b1a39bc549e7
```

The known local snapshot `c5b1779` is stale. The implementation was reviewed
against upstream `c8lab.py`, `API.md`, `SETUP.md`, and `TROUBLESHOOTING.md` at
the fresh SHA before behavior was borrowed. Relevant confirmed patterns are:

- client-credentials token acquisition without logging the secret/token;
- `GET /v2/state/ledger-end` as the authenticated ledger preflight;
- `GET /v2/parties`, followed by strict `isLocal: true` filtering;
- exact hint reuse before allocation;
- explicit `CanActAs` rights verification before submission;
- 401 as authentication and 403 as authorization/party-rights failure.

## Package upload decisions

- The required participant endpoint is raw binary `POST /v2/packages` beneath
  `C8_BASE`.
- `dpm build` only creates the DAR.
- `dpm test` only tests the model in the IDE/test ledger.
- Neither command uploads to the target participant.
- There is no `dpm ledger upload-dar` command.
- Exact registered package-ID presence is idempotent success
  (`already_present`).
- A new upload discovers its target with
  `GET /v2/state/connected-synchronizers`, unless
  `C8_SYNCHRONIZER_ID` is explicitly configured.
- Zero or multiple discovered IDs fail before upload; there is no bare-POST
  fallback, and no identifier is emitted or saved.
- Package-vetting metadata is collision evidence, not connected-synchronizer
  discovery.
- Different code under an already known name/version is
  `PACKAGE_VERSION_COLLISION`, including participant
  `KNOWN_PACKAGE_VERSION`.
- The smallest safe recovery is a Daml-owner patch-version increment, followed
  by a new build, test, package-ID derivation, and upload. Integration code must
  not edit the Daml version itself.

## Required next actions

1. Cherry-pick frozen producer commit
   `60d57f3eb82302c979645b25c6d17ede94834145` into the isolated settlement
   overlay.
2. Adapt the internal module/template/payload/result/query layer to
   `Settlement.Mandate:Charge` and its nested `DemoAsset:Pay`; preserve the ten
   canonical subcommands and one-object JSON envelope.
3. Build the combined source and derive its DAR path and main package ID
   dynamically. Do not reuse any core-only or spike-only package hash.
4. Rerun the final settlement sandbox sequence twice, querying the actual
   mandate, `DEMO-GBP` asset balances, and settlement audit after every rejected
   attack.
5. If DevNet variables become available, rerun the same command in `DEVNET`
   mode. Otherwise retain the truthful `SANDBOX` label and credentials blocker.

## Current completion evidence

| Required field | Current value |
|---|---|
| Branch | `agent/devnet` |
| Frozen producer commit SHA | `60d57f3eb82302c979645b25c6d17ede94834145` |
| Producer parent | `1f8aba1a2dc68f60b5e74fbca8b887e225927103` |
| DAR path | Derived programmatically; exact generated path omitted from the committed handoff |
| Main package ID | Intentionally unpinned; derive from the final combined DAR |
| Target mode | `SANDBOX` |
| Parties used | Owner, Agent, Merchant-A, Merchant-B; exact IDs handed off outside the commit |
| Package upload result | Two fresh processes `uploaded`; separate repeat `already_present` |
| Successful charge update | `COMMITTED`; exact update ID handed off outside the commit |
| Cap rejection raw category | HTTP 400; `DAML_FAILURE`; category 9; gRPC 9; exact cap assertion |
| Allow-list rejection raw category | HTTP 400; `DAML_FAILURE`; category 9; gRPC 9; exact allow-list assertion |
| Post-revocation rejection raw category | HTTP 404; `CONTRACT_NOT_FOUND`; category 11; gRPC 5 |
| Exact one-command invocation | `./scripts/demo_devnet.sh` |
| Unresolved blocker | DevNet variables are unset; settlement adaptation and fresh final proof remain |

The evidence above is real sandbox participant evidence, not DevNet evidence.
Do not relabel its package or committed transactions as DevNet or as final
settlement evidence.
