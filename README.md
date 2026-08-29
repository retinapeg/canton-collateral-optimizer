# Canton Collateral Optimizer

Privacy-preserving collateral optimisation across fragmented financial systems using Python for constrained optimisation and Daml/Canton for permissioned multi-party state, approvals and execution.

## Hackathon demo

From the repository root, the normal complete demo entrypoint is:

```bash
./demo.sh
```

No virtual-environment activation, `PATH` export, package-directory change, or
separate Canton terminal is required. The launcher reuses the persistent local
`.venv`, installs Python dependencies only when the manifest or environment has
changed, discovers DPM, builds both Daml packages, starts a fresh owned Canton
sandbox on dedicated ports, runs the optimiser-to-ledger workflow and wallet
proof, and then waits for its entire sandbox process group to exit.

An unrelated Canton instance on port 7575 is never reused or stopped. Local
runtime logs are written under the gitignored `.run/` directory.

The optional root-level preparation and verification commands are:

```bash
./setup_demo.sh
./test_all.sh
```

The detailed commands later in this README remain useful for component-level
development, but are not required for the complete hackathon demo.

## Collaboration

This repository is private. Every collaborator must be explicitly invited to
the repository before they can access it.

Add a collaborator with write access (the default permission):

```bash
./scripts/add-collaborator.sh <github-username>
```

Specify write or read access explicitly:

```bash
./scripts/add-collaborator.sh <github-username> push
./scripts/add-collaborator.sh <github-username> pull
```

The helper also accepts `maintain` and `admin` when those higher permissions
are deliberately required. It uses the authenticated GitHub CLI session and
does not store credentials in the repository.

## Current status

The minimum end-to-end slice is working:

1. BankA and BankB own private `CollateralOffer` contracts.
2. Allocator is explicitly authorised as an observer and reads both banks'
   offers plus the relevant requirements from Canton.
3. The independent Python optimiser finds the cheapest valid allocation with
   `scipy.optimize.linprog`.
4. Python submits private `ReallocationProposal` contracts, acting as Allocator.
5. BankA exercises the owner-controlled `Accept` choice.
6. Canton atomically archives the old proposal and offer, creates a residual
   offer, and creates the final `CollateralAllocation`.
7. Party-scoped active-contract queries prove that BankB does not receive
   BankA's unrelated offer, proposal, or accepted allocation.

The deterministic result is:

| Requirement | Selected asset | Quantity | Effective value | Cost |
|---|---:|---:|---:|---:|
| REQ-BANK-A-CCP | A-GILT-2030 | 5.1020408163 | 500.00 | 2.5510204082 |
| REQ-BANK-B-CCP | B-CORP-2029 | 5.0000000000 | 400.00 | 2.0000000000 |
| **Total** | | | **900.00** | **4.5510204082** |

The quantity sent to Daml is conservatively rounded upward to ten decimal
places (`5.1020408164`), producing an on-ledger effective value of
`500.0000000072` rather than rounding below the requirement.

## Architecture

```text
Daml contracts on Canton
  CollateralOffer + CollateralRequirement
              |
              | party-scoped JSON Ledger API v2 query as Allocator
              v
backend/  -------- maps only authorised contract payloads --------+
              |                                                   |
              v                                                   |
optimizer/  scipy.optimize.linprog                                |
              |  plain dictionaries; no Canton dependencies      |
              v                                                   |
backend/  creates ReallocationProposal on Canton <----------------+
              |
              | owner-controlled Daml Accept choice
              v
Canton transaction
  archive proposal + archive old offer + create residual offer
  + create CollateralAllocation
```

Repository layout:

```text
daml/                  Daml templates and executable Daml Script tests
optimizer/             ledger-independent linear optimiser
backend/               thin Canton JSON Ledger API v2 adapter and demo runner
tests/                 Python optimiser and mapping tests
sample_data/           deterministic two-bank market
frontend/              intentionally not created until the core is complete
sources/               read-only synced project material; currently empty
```

## Environment audit and chosen tools

The project initially contained no code or hackathon starter files. Python
3.11.5, NumPy 1.26.0, and SciPy 1.11.3 were already available. Daml, Canton,
Java, pytest, Node, and a running Docker daemon were not available.

For the fastest credible path, this project now uses:

- a repository-local Python virtual environment at `.venv`;
- pinned NumPy 1.26.0 and SciPy 1.11.3 dependencies;
- Digital Asset Package Manager (DPM) 3.5.7;
- the DPM-bundled Canton Open Source Sandbox 3.5.14;
- OpenJDK 17;
- Python's built-in `unittest`, avoiding an unnecessary test dependency;
- Python's built-in HTTP client, avoiding an unnecessary web framework or
  third-party HTTP dependency;
- Canton JSON Ledger API v2 rather than legacy JSON API v1 or generated gRPC
  bindings.

The official `skeleton-single-package`, `daml-intro-daml-scripts`, and
`daml-intro-choices` templates were used as syntax and workflow references.
The official propose-and-accept pattern and JSON Ledger API tutorial were also
followed. The full Canton Network Quickstart was deliberately not adopted for
this first slice: it brings Docker, Nix, Gradle, React, Keycloak, wallets,
multiple validators, and substantially more startup risk than this demo needs.

## One-time installation on this Mac

These steps have already been completed on the current machine. They are here
so the demo is reproducible:

```bash
brew install openjdk@21
curl -sSL https://get.daml.com/ -o get-daml.sh && sh get-daml.sh 3.4.10
```

Create the project-local Python environment and install the tested dependencies:

```bash
# We use the 'hack' virtual environment created in the parent directory
cd canton-collateral-optimizer
source ../hack/bin/activate
python -m pip install -r requirements.txt
```

For each new terminal, activate the virtual environment and expose Java and DPM:

```bash
cd canton-collateral-optimizer
source ../hack/bin/activate
export JAVA_HOME=/opt/homebrew/opt/openjdk@21
export PATH="$HOME/.daml/bin:$JAVA_HOME/bin:$PATH"
```

Check the installation:

```bash
java -version
dpm version
```

Expected active SDK/toolchain version: `3.5.7`. (`dpm --version` reports the
package-manager binary's separate version.)

## VS Code workflow

Open this repository folder in VS Code and select `.venv/bin/python` as the
Python interpreter. The local `.vscode/` folder is intentionally ignored because
editor settings and task paths can be machine-specific. Use the tested commands
below in the integrated terminal.

## Exact commands: test, build, and run

Run all commands below from the repository root unless a step explicitly changes
directory.

### 1. Run the Python tests

```bash
python -m unittest discover -s tests -v
```

The suite covers:

- no asset allocated above its available quantity;
- every requirement satisfied;
- ineligible asset classes never used;
- the known cheapest solution;
- clean `INFEASIBLE` output;
- no double allocation across requirements;
- no use of one bank's inventory for another bank's obligation;
- deterministic sample output and JSON serialisability;
- safe conversion to Daml's ten-decimal scale.

### 2. Build and test the Daml model

```bash
export JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
export PATH="$HOME/.dpm/bin:/opt/homebrew/opt/openjdk@17/bin:$PATH"

cd daml
dpm build
dpm test
cd ..
```

Expected DAR:

```text
daml/.daml/dist/collateral-optimizer-0.0.1.dar
```

`dpm test` executes the privacy, controller-authorisation, residual-offer,
acceptance, and stale-proposal assertions on Daml's IDE ledger.

### 3. Start the real Canton Sandbox

Keep this command running in terminal 1:

```bash
cd daml

export JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
export PATH="$HOME/.dpm/bin:/opt/homebrew/opt/openjdk@17/bin:$PATH"

dpm sandbox \
  --json-api-port 7575 \
  --dar .daml/dist/collateral-optimizer-0.0.1.dar
```

This starts a genuine Canton development topology:

- gRPC Ledger API: `localhost:6865`;
- JSON Ledger API v2: `localhost:7575`;
- one Canton participant;
- one local synchronizer.

Health check from another terminal:

```bash
curl --fail http://localhost:7575/livez
curl --fail http://localhost:7575/v2/state/ledger-end
```

Before running the backend, wait until terminal 1 prints:

```text
Canton sandbox is ready.
```

The backend also retries the one known transient case where the HTTP service is
live but party allocation begins just before the participant finishes connecting
to the synchronizer.

### 4. Run the entire Python → Canton → Daml demo

In terminal 2:

```bash
python -m backend.demo
```

The command:

1. allocates or discovers BankA, BankB, Allocator, and CCP;
2. seeds missing sample offers and requirements on Canton;
3. captures and verifies each party's private pre-optimisation view;
4. reads the Allocator-authorised ledger state;
5. calls the independent optimiser;
6. creates one private proposal per allocation;
7. verifies BankA and BankB each see only their own proposal;
8. exercises BankA's `Accept` choice as BankA;
9. verifies the state transition and post-acceptance privacy;
10. prints a machine-readable demonstration summary.

Successful output ends with:

```json
{
  "status": "DEMO_COMPLETE",
  "privacy_scope": "Daml party-level stakeholder visibility",
  "topology": "Canton Sandbox: one participant and one local synchronizer"
}
```

The actual output includes all allocations, party views, full party IDs,
effective values, the residual quantity (`4.8979591836`), and the Canton ledger
end.

The runner intentionally refuses to execute when active proposals or
allocations already exist. Stop the Sandbox with `Ctrl-C`, restart it, and run
the command again for a clean deterministic demonstration.

## Optional: run the full Daml proof against Canton itself

This is separate from `dpm test`. It submits the same privacy and acceptance
script to a live Canton Sandbox over the gRPC Ledger API.

Start a **fresh** Sandbox as described above, then run in terminal 2:

```bash
cd daml

export JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
export PATH="$HOME/.dpm/bin:/opt/homebrew/opt/openjdk@17/bin:$PATH"

dpm script \
  --dar .daml/dist/collateral-optimizer-0.0.1.dar \
  --script-name Demo:privacyAndAcceptance \
  --ledger-host localhost \
  --ledger-port 6865
```

Use a different fresh Sandbox for `python -m backend.demo`. The Daml proof
allocates the same four party hints and deliberately leaves proposal/allocation
test state; the Python runner rejects any pre-existing proposal/allocation state.

## Ninety-second demonstration script

1. Point to `before_optimisation.BankA` and `before_optimisation.BankB` in the
   output: each bank has only its own offers and requirement.
2. Point to `before_optimisation.Allocator`: it has all five authorised offers
   and both requirements.
3. Point to `optimisation`: status is `OPTIMAL`, both coverage checks are true,
   and total cost is `4.551020408163265`.
4. Point to `private_proposals`: BankA sees only `A-GILT-2030`; BankB sees only
   `B-CORP-2029`.
5. Point to `bank_a_accepted`: BankA authorised 5.1020408164 units and Canton
   recorded 500.0000000072 effective value.
6. Point to `after_acceptance.BankA`: the allocation and residual offer exist;
   the proposal is gone.
   `bank_a_residual_offers` shows the remaining quantity is exactly
   `4.8979591836`.
7. Point to `after_acceptance.BankB`: BankB still sees only its own offer,
   requirement, and proposal—no BankA allocation.
8. Close with the one-line pitch at the top of this README.

## Daml ownership, visibility, and execution

| Contract | Signatory | Observers | Intended visibility |
|---|---|---|---|
| BankA `CollateralOffer` | BankA | Allocator | BankA, Allocator |
| BankB `CollateralOffer` | BankB | Allocator | BankB, Allocator |
| BankA `CollateralRequirement` | CCP | BankA, Allocator | CCP, BankA, Allocator |
| BankB `CollateralRequirement` | CCP | BankB, Allocator | CCP, BankB, Allocator |
| BankA `ReallocationProposal` | Allocator | BankA | Allocator, BankA |
| BankB `ReallocationProposal` | Allocator | BankB | Allocator, BankB |
| BankA `CollateralAllocation` | BankA, Allocator | CCP | BankA, Allocator, CCP |

The proposal stores the exact `offerCid` and `requirementCid` read by the
optimiser. Neither the owner nor Allocator can create the bilateral final state
alone. On `Accept`, Daml revalidates owner, allocator, asset ID, requirement
ID, obligor, asset-class eligibility, and available quantity. It then performs
the archive/recreate/allocation transition atomically.

If two proposals refer to the same offer contract ID, accepting one consumes
that offer. The second proposal becomes stale and fails. This makes Canton
responsible for stale-state and double-use protection even if an off-ledger
client is buggy or out of date.

## Optimisation interface and mathematics

The optimiser accepts ordinary dictionaries shaped like:

```json
{
  "assets": [
    {
      "asset_id": "A-GILT-2030",
      "owner": "BankA",
      "asset_class": "GOVERNMENT_BOND",
      "market_value": 100.0,
      "haircut": 0.02,
      "opportunity_cost": 0.5,
      "available_quantity": 10.0,
      "location": "Custodian-A"
    }
  ],
  "requirements": [
    {
      "requirement_id": "REQ-BANK-A-CCP",
      "obligor": "BankA",
      "beneficiary": "CCP",
      "required_effective_value": 500.0,
      "eligible_asset_classes": ["GOVERNMENT_BOND", "CASH"]
    }
  ]
}
```

It returns:

```json
{
  "status": "OPTIMAL",
  "total_cost": 2.5510204081632653,
  "allocations": [
    {
      "asset_id": "A-GILT-2030",
      "owner": "BankA",
      "requirement_id": "REQ-BANK-A-CCP",
      "quantity": 5.1020408163265305,
      "effective_value": 500.0,
      "cost": 2.5510204081632653
    }
  ],
  "requirement_coverage": [
    {
      "requirement_id": "REQ-BANK-A-CCP",
      "required_effective_value": 500.0,
      "allocated_effective_value": 500.0,
      "satisfied": true
    }
  ]
}
```

The LP minimises:

```text
sum(i,j) opportunity_cost(i) * quantity(i,j)
```

subject to:

```text
sum(i) market_value(i) * (1 - haircut(i)) * quantity(i,j)
  >= required_effective_value(j)

sum(j) quantity(i,j) <= available_quantity(i)
quantity(i,j) >= 0
```

An asset/requirement pair is omitted when the asset class is ineligible or the
asset owner is not the requirement obligor. The per-asset sum across all
requirements is the no-double-allocation constraint.

Run the optimiser alone with:

```bash
python -m optimizer sample_data/market.json
```

## Canton API surface used

The backend deliberately uses only a small JSON Ledger API v2 surface:

- `GET /v2/state/ledger-end`
- `GET /v2/parties`
- `POST /v2/parties`
- `POST /v2/state/active-contracts-page`
- `POST /v2/commands/submit-and-wait-for-transaction`

The DAR is supplied directly to `dpm sandbox`, so no separate package-upload
endpoint is required.

## Privacy claim: precise wording

The current demo proves **Daml party-level stakeholder visibility and Canton
execution**. The Sandbox is real Canton, but it hosts all four parties on one
participant and has request authentication disabled by default.

Therefore the correct claim is:

> BankA does not receive BankB-only contracts in its Daml/Canton party view,
> and BankB does not receive BankA-only contracts in its party view.

The current topology does **not** prove that an operator with unrestricted
access to the single participant's unauthenticated API cannot request another
locally hosted party's view. Do not describe the Sandbox as four physically
separate bank nodes.

The next privacy hardening step is either:

1. enable Ledger API authentication and create separate users/JWTs with only
   the appropriate `CanReadAs` and `CanActAs` rights; or
2. deploy BankA and BankB on separate Canton validator/participant nodes.

The second option is the stronger institution/operator-isolation demo, but the
official multi-validator Quickstart is deliberately outside the first vertical
slice.

## Deliberate first-slice limits

- No frontend. The machine-readable terminal output is the demo surface until
  the core flow is stable.
- No market shock or re-optimisation workflow yet.
- No reinforcement learning.
- A requirement remains active after one allocation. The demo runner refuses
  to run again if allocations/proposals exist, preventing accidental reposting
  in this first version.
- The Python optimiser proves plan-level requirement coverage. Daml revalidates
  and authorises each individual allocation line, but this first contract model
  does not aggregate a multi-line plan on-ledger and therefore does not itself
  prove that the whole requirement is satisfied. A future batch-plan contract
  should do that without incorrectly requiring every individual line to cover
  the full requirement.
- `CollateralAllocation` is an owner-and-Allocator agreed allocation/earmarking
  state observed by CCP. It is not yet CCP-approved settlement confirmation or
  evidence that a custodian physically moved the asset. That stronger state
  requires a CCP-controlled settlement choice or an integration with the
  relevant custody/settlement contract.
- Pre-creating several partial proposals against the same offer CID makes later
  proposals stale after the first is accepted. The sample deliberately uses a
  different offer for each accepted allocation. A batch plan/lock contract can
  solve this later if the core story needs multi-requirement splits.
- `CollateralRequirement` is a CCP-published request with CCP as signatory. The
  bank gives its authority when it accepts the resulting allocation. Making the
  requirement itself bilateral would require another invite/accept flow.
- The Daml script dependency lives in the same DAR for hackathon speed, so the
  compiler emits a harmless package-store-size warning. Split tests into a
  second package only after the demo.

## Official references

- [DPM command and configuration reference](https://docs.canton.network/sdks-tools/cli-tools/dpm)
- [Canton Sandbox](https://docs.canton.network/sdks-tools/development-tools/sandbox)
- [JSON Ledger API v2 migration/reference guide](https://docs.canton.network/sdks-tools/api-reference/json-ledger-api-migration-to-v2)
- [Canton ledger model: stakeholders, choices, and atomic transactions](https://docs.canton.network/overview/learn/ledger-model)
