# Integration status

Checklist kept during the hackathon (2026-08-29) for the first collateral
vertical slice. Unless marked otherwise, entries record what was verified on
that day.

## WORKING

- Vertical slice verified on 2026-08-29 against a fresh Canton Sandbox: private `CollateralOffer`/`CollateralRequirement` state -> authorised Allocator view -> Python optimiser -> private `ReallocationProposal` -> BankA-controlled `Accept` -> archived proposal/original offer plus residual `CollateralOffer` and new `CollateralAllocation`.
- Optimiser/backend/Daml contracts agree on party ownership, asset and requirement IDs, eligibility, quantities, contract IDs, and Daml's 10-decimal quantity scale.
- Python tests: 72/72 passing with `python -m unittest discover -s tests` and `python -m pytest -q tests` (re-run offline on 2026-09-22). The earlier 17/17 figure predates the allocation-demo and agent-wallet tests.
- `dpm build` and `dpm test`: passing; `Demo:privacyAndAcceptance` proves visibility, controller authority, residual state and stale-proposal rejection.
- `python -m backend.demo`: `DEMO_COMPLETE`; accepted `A-GILT-2030` quantity `5.1020408164`, effective value `500.0000000072`, residual `4.8979591836`.
- Reproducible command sequence is documented in `README.md` under **Component commands**; `./demo.sh` runs the complete ledger demo.

## IN PROGRESS

- Changes to any one component remain integration-sensitive; re-run the checks above whenever optimiser, backend mapping, Daml templates, sample data or package/toolchain configuration changes.

## BROKEN

- None detected in the current first vertical slice.

## BLOCKERS

- None for the stated demonstration.
- Known boundary: Python validates whole-plan coverage, while Daml currently authorises and revalidates each allocation line rather than an atomic aggregate plan. This is documented and is not blocking the single accepted-allocation demo.

## NEXT INTEGRATION STEP

- Preserve the current optimiser/backend/Daml interfaces and re-run the full live demo after the next parallel change; prefer a small adapter if any incoming field or payload shape differs.
