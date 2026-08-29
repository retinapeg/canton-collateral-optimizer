"""Connect the deterministic global optimiser result to a bilateral Daml flow."""

from __future__ import annotations

import argparse
from decimal import Decimal
from typing import Any

from backend.canton import ActiveContract, CantonClient, LedgerApiError
from optimizer import AllocationResult, optimize_allocation


PACKAGE_NAME = "collateral-optimizer"
MODULE_NAME = "CollateralAllocation"
ALLOCATION_REFERENCE = "optimizer-allocation-001"
LEDGER_ASSET_BY_OPTIMIZER_ASSET = {"Asset2": "UK_GILT_2035"}
LEDGER_PARTY_BY_DESTINATION = {"InstitutionB": "BankB"}
LEDGER_QUANTITY = Decimal("30")
LEDGER_QUANTITY_WIRE = format(
    LEDGER_QUANTITY.quantize(Decimal("0.0000000001")), "f"
)


def template_id(name: str) -> str:
    return f"#{PACKAGE_NAME}:{MODULE_NAME}:{name}"


def run_optimizer() -> tuple[AllocationResult, str, str]:
    supplies = {"Asset1": 1.0, "Asset2": 1.0}
    demands = {"InstitutionB": 1.0, "InstitutionC": 1.0}
    costs = {
        "Asset1": {"InstitutionB": 1.0, "InstitutionC": 2.0},
        "Asset2": {"InstitutionB": 1.1, "InstitutionC": 100.0},
    }
    haircuts = {
        asset_id: {destination_id: 0.0 for destination_id in demands}
        for asset_id in supplies
    }
    eligibility = {
        asset_id: {destination_id: True for destination_id in demands}
        for asset_id in supplies
    }
    result = optimize_allocation(
        supplies=supplies,
        demands=demands,
        costs=costs,
        haircuts=haircuts,
        eligibility=eligibility,
    )
    if not result.success or result.total_cost is None:
        raise RuntimeError(f"Optimizer failed: {result.status} - {result.message}")

    destination = "InstitutionB"
    selected_assets = [
        asset_id
        for asset_id in supplies
        if result.allocations[asset_id][destination] > 0.0
    ]
    if selected_assets != ["Asset2"]:
        raise RuntimeError(
            f"Expected Asset2 for InstitutionB, got {selected_assets!r}"
        )
    return result, selected_assets[0], destination


def find_one(
    contracts: list[ActiveContract], template_name: str
) -> ActiveContract:
    matches = [
        contract
        for contract in contracts
        if contract.template_name == template_name
        and contract.payload.get("allocationReference") == ALLOCATION_REFERENCE
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one {template_name} for {ALLOCATION_REFERENCE}, "
            f"found {len(matches)}"
        )
    return matches[0]


def run_ledger_flow(
    client: CantonClient, *, asset: str, recipient_hint: str
) -> ActiveContract:
    client.ledger_end()
    source = client.ensure_party("BankA")
    recipient = client.ensure_party(recipient_hint)

    existing = [
        contract
        for contract in client.active_contracts(recipient)
        if contract.template_name in {"AllocationProposal", "AllocatedCollateral"}
        and contract.payload.get("allocationReference") == ALLOCATION_REFERENCE
    ]
    if existing:
        raise RuntimeError(
            "This deterministic demo requires a fresh Sandbox without "
            f"{ALLOCATION_REFERENCE}"
        )

    client.create(
        act_as=source,
        template_id=template_id("AllocationProposal"),
        arguments={
            "source": source,
            "recipient": recipient,
            "asset": asset,
            "quantity": LEDGER_QUANTITY_WIRE,
            "allocationReference": ALLOCATION_REFERENCE,
        },
        label="bank-a-propose-allocation",
    )

    proposal = find_one(client.active_contracts(recipient), "AllocationProposal")
    client.exercise(
        act_as=recipient,
        template_id=template_id("AllocationProposal"),
        contract_id=proposal.contract_id,
        choice="Accept",
        label="bank-b-accept-allocation",
    )

    recipient_view = client.active_contracts(recipient)
    if any(contract.contract_id == proposal.contract_id for contract in recipient_view):
        raise RuntimeError("Accepted proposal is still active")
    allocation = find_one(recipient_view, "AllocatedCollateral")
    expected: dict[str, Any] = {
        "source": source,
        "recipient": recipient,
        "asset": asset,
        "quantity": LEDGER_QUANTITY_WIRE,
        "allocationReference": ALLOCATION_REFERENCE,
    }
    if allocation.payload != expected:
        raise RuntimeError(
            f"Accepted allocation does not match the proposal: {allocation.payload!r}"
        )
    if set(allocation.signatories) != {source, recipient}:
        raise RuntimeError("Accepted allocation does not have both bank signatories")
    return allocation


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the optimizer-selected Bank A to Bank B Daml allocation"
    )
    parser.add_argument("--base-url", default="http://localhost:7575")
    args = parser.parse_args()

    try:
        result, optimizer_asset, destination = run_optimizer()
        asset = LEDGER_ASSET_BY_OPTIMIZER_ASSET[optimizer_asset]
        recipient_hint = LEDGER_PARTY_BY_DESTINATION[destination]
        run_ledger_flow(
            CantonClient(args.base_url),
            asset=asset,
            recipient_hint=recipient_hint,
        )
    except (KeyError, LedgerApiError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"ALLOCATION DEMO FAILED: {exc}") from exc

    local_cost = 101.0
    savings = local_cost - result.total_cost
    print("GLOBAL OPTIMIZER")
    print(f"\nLocal allocation cost: {local_cost:.1f}")
    print(f"Global optimum cost: {result.total_cost:.1f}")
    print(f"Savings: {savings:.1f}")
    print("\nOPTIMIZER SELECTED")
    print(f"\n{optimizer_asset} -> {destination}")
    print("\nMAPPED LEDGER ACTION")
    print(f"\nBankA -> {recipient_hint}")
    print(f"Asset: {asset}")
    print(f"Quantity: {LEDGER_QUANTITY}")
    print(f"Reference: {ALLOCATION_REFERENCE}")
    print("\nBank B received allocation:")
    print(f"{LEDGER_QUANTITY} {asset}")


if __name__ == "__main__":
    main()
