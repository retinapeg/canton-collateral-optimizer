# Canton Collateral Optimizer

Privacy-preserving collateral optimisation across fragmented financial systems
using Python for constrained optimisation and Daml/Canton for permissioned
multi-party state, approvals and execution.

A hackathon prototype built at Oxford Hack in August 2026. A ledger-independent
Python optimiser (`scipy.optimize.linprog`) finds the cheapest valid way to
allocate collateral against institutions' requirements. A thin adapter then
turns each allocation into a Daml propose-and-accept contract on a local Canton
sandbox, where only the receiving party can accept and each bank sees only its
own contracts. The repository also contains `agent_wallet/`, a spend-limited
wallet for an AI agent built by a teammate (see [Who built what](#who-built-what)).

## Results at a glance

| What | Result | Reproduce / checked by |
|---|---|---|
| Illustrative greedy counter-example (two assets, two institutions) | greedy allocation scores 101.0; the global LP optimum scores 3.1 (about 96.9% lower) | `python -m optimizer`; `tests/test_optimizer.py`, `tests/test_allocation_demo.py` |
| Two-bank sample market (`sample_data/market.json`) | both requirements covered (500 + 400 effective value) at total cost 4.5510 | `python -m optimizer sample_data/market.json`; `tests/test_optimizer.py` |
| Python test suite | 72 tests pass: optimiser 11, Canton adapter 8, allocation demo 14, agent wallet 39 | `python -m pytest -q tests` |

The Python tests run offline and do not need Canton. The Daml Script tests and
the live ledger demo need DPM and Java (see
[Prerequisites](#prerequisites-for-the-ledger-demo)).

## Quick start

Offline, with Python 3 (developed on 3.11):

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest -q tests              # 72 tests, no ledger needed
.venv/bin/python -m optimizer                    # greedy 101.0 versus optimum 3.1
```

Full demo against a fresh local Canton sandbox:

```bash
./demo.sh
```

No virtual-environment activation, `PATH` export, package-directory change, or
separate Canton terminal is required. The launcher creates or reuses the
repository-local `.venv`, installs Python dependencies only when
`requirements.txt` or the environment has changed, discovers DPM and Java,
builds both Daml packages, starts a fresh sandbox that it owns on dedicated
ports (16865-16869 and 17575), loads both DARs, and then runs:

1. `backend.allocation_demo`: the optimiser's two allocations are mapped to
   ledger instructions (BankA sends 30 `US_TREASURY_2034` to BankB and 20
   `UK_GILT_2035` to BankC) and created as `AllocationProposal` contracts; the
   runner checks that BankA cannot exercise BankB's `Accept` choice, has each
   recipient accept, queries the ledger and reconciles the result against the
   optimiser output;
2. `agent_wallet.demo`: the spend-limited wallet story and attack suite.

It then stops its entire sandbox process group and writes
`artifacts/demo_run.json` and `artifacts/demo_run.csv` (both gitignored). An
unrelated Canton instance on port 7575 is never reused or stopped. Runtime logs
go to the gitignored `.run/` directory.

The optional root-level preparation and verification commands are:

```bash
./setup_demo.sh   # toolchain check, .venv, dependencies, Daml builds
./test_all.sh     # shell syntax, pytest, dpm test for both Daml packages, git diff --check
```

## Who built what

- The collateral optimiser (`optimizer/`), the Canton adapter and demo runners
  (`backend/`), the collateral Daml package (`daml/`) and the root launcher
  scripts were written by the repository owner (git author `retinapeg`).
- `agent_wallet/`, a spend-limited wallet for an AI agent (Oxford Hack Daml
  track D1), was built by teammate Arkajyoti Saha, who also extended
  `backend/canton.py` for it; see [`agent_wallet/README.md`](agent_wallet/README.md).
  `./demo.sh` runs the wallet on the same sandbox as the collateral flow.
- AI coding assistants were used during development.
  [`agent_wallet/AGENTS.md`](agent_wallet/AGENTS.md) is the working brief
  written for them on the wallet subproject; the root project's local assistant
  metadata is gitignored (see `.gitignore`).

## Repository layout

```text
optimizer/             ledger-independent linear optimiser
backend/               thin Canton JSON Ledger API v2 client and the two demo runners
daml/                  collateral Daml package (SDK 3.5.7) and Daml Script tests
agent_wallet/          teammate's spend-limited AI-agent wallet (Daml SDK 3.4.10, Python, MCP server)
tests/                 Python tests for the optimiser, adapter, allocation demo and wallet
sample_data/           deterministic two-bank market
scripts/               shared launcher helpers, plus add-collaborator.sh (grants a
                       GitHub user access through the authenticated gh CLI)
demo.sh                complete ledger demo
setup_demo.sh          toolchain check, .venv, dependencies, Daml builds
test_all.sh            full verification, including dpm test
run_sim.sh             teammate's wallet-simulation helper; it hard-codes an Intel
                       Homebrew OpenJDK 21 path and the legacy daml assistant
INTEGRATION_STATUS.md  integration checklist kept during the hackathon
.mcp.json              registers agent_wallet's MCP server for MCP-aware editors
```

## The two collateral flows

Both runners share the optimiser and the Canton client:

| Runner | Daml module | Scenario | Started by |
|---|---|---|---|
| `backend.allocation_demo` | `CollateralAllocation` (`AllocationProposal`, `AllocatedCollateral`) | greedy counter-example; BankA allocates to BankB and BankC | `./demo.sh` |
| `backend.demo` | `Collateral` (`CollateralOffer`, `CollateralRequirement`, `ReallocationProposal`, `CollateralAllocation`) | two-bank sample market with CCP requirements, residual offers and party-view privacy checks | manually, see [Component commands](#component-commands) |

### First vertical slice: `backend.demo`

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

## Toolchain

- Python 3 with NumPy, SciPy and pytest (`requirements.txt`); Python's
  built-in HTTP client, with no web framework or third-party HTTP dependency;
- Digital Asset Package Manager (DPM), using SDK 3.5.7 for `daml/` and 3.4.10
  for `agent_wallet/` (the `sdk-version` in each `daml.yaml`);
- the DPM-bundled Canton open-source sandbox (developed against 3.5.14);
- OpenJDK 17 or 21;
- Canton JSON Ledger API v2 rather than legacy JSON API v1 or generated gRPC
  bindings.

The official `skeleton-single-package`, `daml-intro-daml-scripts`, and
`daml-intro-choices` templates were used as syntax and workflow references.
The official propose-and-accept pattern and JSON Ledger API tutorial were also
followed. The full Canton Network Quickstart was deliberately not adopted for
this first slice: it brings Docker, Nix, Gradle, React, Keycloak, wallets,
multiple validators, and substantially more startup risk than this demo needs.

## Prerequisites for the ledger demo

The root scripts (`scripts/demo_common.sh`) look for:

- `python3` on `PATH`, used to create `.venv`;
- DPM at `$HOME/.dpm/bin/dpm` or on `PATH` (or set `DEMO_DPM_BIN`); install it
  by following the [DPM reference](https://docs.canton.network/sdks-tools/cli-tools/dpm);
- Java: `$JAVA_HOME` if it contains `bin/java`; otherwise Homebrew OpenJDK 17,
  then 21, under `/opt/homebrew/opt/openjdk@<version>/libexec/openjdk.jdk/Contents/Home`
  and then the same path under `/usr/local/opt` (for example after
  `brew install openjdk@17`);
- `curl`, and `lsof` or `nc` for the port-safety check.

## Component commands

For the manual steps below, set up the same environment the scripts use, from
the repository root:

```bash
source .venv/bin/activate      # created by ./setup_demo.sh or the quick start
export JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
# Intel Macs: /usr/local/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home
export PATH="$HOME/.dpm/bin:$JAVA_HOME/bin:$PATH"

java -version
(cd daml && dpm version)
```

The active SDK reported inside `daml/` should be `3.5.7`. (`dpm --version`
reports the package-manager binary's separate version.)

In VS Code, select `.venv/bin/python` as the interpreter. The local `.vscode/`
folder is intentionally ignored because editor settings can be machine-specific.

### 1. Run the Python tests

```bash
python -m pytest -q tests
# or, with the standard library runner:
python -m unittest discover -s tests -v
```

The 72 tests cover, among other things:

- no asset allocated above its available quantity;
- every requirement satisfied;
- ineligible asset classes never used;
- the known cheapest solution, and the global optimum beating the greedy
  counter-example (3.1 against 101.0);
- clean `INFEASIBLE` output;
- no double allocation across requirements;
- no use of one bank's inventory for another bank's obligation;
- deterministic sample output and JSON serialisability;
- safe conversion to Daml's ten-decimal scale;
- the optimiser-to-ledger mapping, recipient-only acceptance and
  reconciliation in `backend.allocation_demo` (against a fake ledger client);
- the wallet's encoding, error classification, statement rendering, simulated
  workload and MCP tool schemas.

### 2. Build and test the Daml model

```bash
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

### 3. Start a Canton sandbox for `backend.demo`

Keep this command running in terminal 1 (with the environment above):

```bash
cd daml
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

### 4. Run the Python -> Canton -> Daml slice

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

### Optional: run the full Daml proof against Canton itself

This is separate from `dpm test`. It submits the same privacy and acceptance
script to a live Canton Sandbox over the gRPC Ledger API.

Start a **fresh** Sandbox as described above, then run in terminal 2:

```bash
cd daml
dpm script \
  --dar .daml/dist/collateral-optimizer-0.0.1.dar \
  --script-name Demo:privacyAndAcceptance \
  --ledger-host localhost \
  --ledger-port 6865
```

Use a different fresh Sandbox for `python -m backend.demo`. The Daml proof
allocates the same four party hints and deliberately leaves proposal/allocation
test state; the Python runner rejects any pre-existing proposal/allocation state.

### Reading the `backend.demo` output

1. `before_optimisation.BankA` and `before_optimisation.BankB`: each bank has
   only its own offers and requirement.
2. `before_optimisation.Allocator`: it has all five authorised offers and both
   requirements.
3. `optimisation`: status is `OPTIMAL`, both coverage checks are true, and total
   cost is `4.551020408163265`.
4. `private_proposals`: BankA sees only `A-GILT-2030`; BankB sees only
   `B-CORP-2029`.
5. `bank_a_accepted`: BankA authorised 5.1020408164 units and Canton recorded
   500.0000000072 effective value.
6. `after_acceptance.BankA`: the allocation and residual offer exist; the
   proposal is gone. `bank_a_residual_offers` shows the remaining quantity is
   exactly `4.8979591836`.
7. `after_acceptance.BankB`: BankB still sees only its own offer, requirement,
   and proposal, and no BankA allocation.

## Daml ownership, visibility, and execution

In the `Collateral` module used by `backend.demo`:

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

In the `CollateralAllocation` module used by `./demo.sh`, an
`AllocationProposal` is signed by the source bank with the recipient as
observer, and its `Accept` choice is `controller recipient`. The resulting
`AllocatedCollateral` is signed by both source and recipient.

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
- `POST /v2/state/active-contracts-page`, falling back to
  `POST /v2/state/active-contracts` when the paged route returns 404 (Canton
  3.4, used by the wallet package)
- `POST /v2/commands/submit-and-wait-for-transaction`

For the manual `backend.demo` flow the DAR is supplied directly to
`dpm sandbox`. `./demo.sh` instead loads both DARs into its own sandbox with
`POST /v2/packages`.

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

- No frontend for the collateral flow. The machine-readable terminal output is
  the demo surface (the wallet's `agent_wallet/serve.py` page is separate).
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
  compiler emits a harmless package-store-size warning. Moving the tests into a
  second package would remove it.

## Official references

- [DPM command and configuration reference](https://docs.canton.network/sdks-tools/cli-tools/dpm)
- [Canton Sandbox](https://docs.canton.network/sdks-tools/development-tools/sandbox)
- [JSON Ledger API v2 migration/reference guide](https://docs.canton.network/sdks-tools/api-reference/json-ledger-api-migration-to-v2)
- [Canton ledger model: stakeholders, choices, and atomic transactions](https://docs.canton.network/overview/learn/ledger-model)
