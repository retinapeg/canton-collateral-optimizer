"""Run the end-to-end collateral demo against a live Canton Sandbox."""

from __future__ import annotations

import argparse
from decimal import Decimal, ROUND_CEILING
import json
from pathlib import Path
from typing import Any

from backend.canton import ActiveContract, CantonClient, LedgerApiError
from optimizer import optimize_collateral


PACKAGE_NAME = "collateral-optimizer"
DECIMAL_QUANTUM = Decimal("0.0000000001")
PARTY_HINTS = ("BankA", "BankB", "Allocator", "CCP")


def template_id(name: str) -> str:
    return f"#{PACKAGE_NAME}:Collateral:{name}"


def party_hint(party: str) -> str:
    return party.split("::", 1)[0]


def daml_decimal(value: Any, *, round_up: bool = False) -> str:
    rounding = ROUND_CEILING if round_up else None
    decimal_value = Decimal(str(value)).quantize(
        DECIMAL_QUANTUM,
        rounding=rounding,
    )
    return format(decimal_value, "f")


def contracts_of_type(
    contracts: list[ActiveContract], template_name: str
) -> list[ActiveContract]:
    return [contract for contract in contracts if contract.template_name == template_name]


def ensure_demo_parties(client: CantonClient) -> dict[str, str]:
    return {hint: client.ensure_party(hint) for hint in PARTY_HINTS}


def seed_market(
    client: CantonClient,
    parties: dict[str, str],
    sample: dict[str, Any],
) -> None:
    allocator_view = client.active_contracts(parties["Allocator"])
    offers = contracts_of_type(allocator_view, "CollateralOffer")
    requirements = contracts_of_type(allocator_view, "CollateralRequirement")

    for asset in sample["assets"]:
        existing = [
            contract
            for contract in offers
            if contract.payload.get("assetId") == asset["asset_id"]
        ]
        if len(existing) > 1:
            raise RuntimeError(f"Duplicate active offer for {asset['asset_id']}")
        if existing:
            continue
        owner = parties[asset["owner"]]
        client.create(
            act_as=owner,
            template_id=template_id("CollateralOffer"),
            arguments={
                "owner": owner,
                "allocator": parties["Allocator"],
                "assetId": asset["asset_id"],
                "assetClass": asset["asset_class"],
                "marketValue": daml_decimal(asset["market_value"]),
                "haircut": daml_decimal(asset["haircut"]),
                "opportunityCost": daml_decimal(asset["opportunity_cost"]),
                "availableQuantity": daml_decimal(asset["available_quantity"]),
                "location": asset["location"],
            },
            label=f"seed-offer-{asset['asset_id'].lower()}",
        )

    for requirement in sample["requirements"]:
        existing = [
            contract
            for contract in requirements
            if contract.payload.get("requirementId")
            == requirement["requirement_id"]
        ]
        if len(existing) > 1:
            raise RuntimeError(
                f"Duplicate active requirement for {requirement['requirement_id']}"
            )
        if existing:
            continue
        beneficiary = parties[requirement["beneficiary"]]
        client.create(
            act_as=beneficiary,
            template_id=template_id("CollateralRequirement"),
            arguments={
                "obligor": parties[requirement["obligor"]],
                "beneficiary": beneficiary,
                "allocator": parties["Allocator"],
                "requirementId": requirement["requirement_id"],
                "requiredEffectiveValue": daml_decimal(
                    requirement["required_effective_value"]
                ),
                "eligibleAssetClasses": requirement["eligible_asset_classes"],
            },
            label=f"seed-requirement-{requirement['requirement_id'].lower()}",
        )


def read_allocator_market(
    client: CantonClient, allocator: str
) -> tuple[dict[str, Any], dict[str, ActiveContract], dict[str, ActiveContract]]:
    allocator_view = client.active_contracts(allocator)
    offers = contracts_of_type(allocator_view, "CollateralOffer")
    requirements = contracts_of_type(allocator_view, "CollateralRequirement")

    market = {
        "assets": [
            {
                "asset_id": offer.payload["assetId"],
                "owner": party_hint(offer.payload["owner"]),
                "asset_class": offer.payload["assetClass"],
                "market_value": float(offer.payload["marketValue"]),
                "haircut": float(offer.payload["haircut"]),
                "opportunity_cost": float(offer.payload["opportunityCost"]),
                "available_quantity": float(offer.payload["availableQuantity"]),
                "location": offer.payload["location"],
            }
            for offer in offers
        ],
        "requirements": [
            {
                "requirement_id": requirement.payload["requirementId"],
                "obligor": party_hint(requirement.payload["obligor"]),
                "beneficiary": party_hint(requirement.payload["beneficiary"]),
                "required_effective_value": float(
                    requirement.payload["requiredEffectiveValue"]
                ),
                "eligible_asset_classes": requirement.payload[
                    "eligibleAssetClasses"
                ],
            }
            for requirement in requirements
        ],
    }
    offers_by_asset = {offer.payload["assetId"]: offer for offer in offers}
    requirements_by_id = {
        requirement.payload["requirementId"]: requirement
        for requirement in requirements
    }
    if len(offers_by_asset) != len(offers):
        raise RuntimeError("Active collateral asset IDs must be unique")
    if len(requirements_by_id) != len(requirements):
        raise RuntimeError("Active requirement IDs must be unique")
    return market, offers_by_asset, requirements_by_id


def create_proposals(
    client: CantonClient,
    parties: dict[str, str],
    result: dict[str, Any],
    offers_by_asset: dict[str, ActiveContract],
    requirements_by_id: dict[str, ActiveContract],
) -> list[ActiveContract]:
    allocator = parties["Allocator"]
    for allocation in result["allocations"]:
        offer = offers_by_asset[allocation["asset_id"]]
        requirement = requirements_by_id[allocation["requirement_id"]]
        owner = parties[allocation["owner"]]
        client.create(
            act_as=allocator,
            template_id=template_id("ReallocationProposal"),
            arguments={
                "allocator": allocator,
                "owner": owner,
                "offerCid": offer.contract_id,
                "requirementCid": requirement.contract_id,
                "assetId": allocation["asset_id"],
                "requirementId": allocation["requirement_id"],
                # Round upward at Daml's 10-decimal scale so coverage is never
                # lost when converting a floating-point LP solution.
                "quantity": daml_decimal(allocation["quantity"], round_up=True),
            },
            label=f"propose-{allocation['asset_id'].lower()}",
        )

    current = contracts_of_type(
        client.active_contracts(allocator), "ReallocationProposal"
    )
    proposals = []
    for allocation in result["allocations"]:
        matches = [
            proposal
            for proposal in current
            if proposal.payload["assetId"] == allocation["asset_id"]
            and proposal.payload["requirementId"] == allocation["requirement_id"]
        ]
        if len(matches) != 1:
            raise RuntimeError(
                "Expected exactly one proposal for "
                f"{allocation['asset_id']} -> {allocation['requirement_id']}"
            )
        proposals.append(matches[0])
    return proposals


def contract_view(contracts: list[ActiveContract]) -> dict[str, list[str]]:
    view: dict[str, list[str]] = {}
    for contract in contracts:
        identifier = (
            contract.payload.get("assetId")
            or contract.payload.get("requirementId")
            or contract.contract_id[:12]
        )
        view.setdefault(contract.template_name, []).append(str(identifier))
    return {name: sorted(values) for name, values in sorted(view.items())}


def party_view(client: CantonClient, party: str) -> dict[str, list[str]]:
    return contract_view(client.active_contracts(party))


def assert_initial_authorised_views(
    client: CantonClient,
    parties: dict[str, str],
    sample: dict[str, Any],
) -> dict[str, dict[str, list[str]]]:
    contracts = {
        hint: client.active_contracts(parties[hint])
        for hint in ("BankA", "BankB", "Allocator")
    }
    expected_all_assets = {asset["asset_id"] for asset in sample["assets"]}
    expected_all_requirements = {
        requirement["requirement_id"] for requirement in sample["requirements"]
    }

    for bank in ("BankA", "BankB"):
        bank_party = parties[bank]
        visible_offers = contracts_of_type(contracts[bank], "CollateralOffer")
        visible_requirements = contracts_of_type(
            contracts[bank], "CollateralRequirement"
        )
        expected_offers = {
            asset["asset_id"]
            for asset in sample["assets"]
            if asset["owner"] == bank
        }
        expected_requirements = {
            requirement["requirement_id"]
            for requirement in sample["requirements"]
            if requirement["obligor"] == bank
        }
        if {offer.payload["assetId"] for offer in visible_offers} != expected_offers:
            raise RuntimeError(
                f"{bank} does not have exactly its expected private collateral view"
            )
        if any(offer.payload["owner"] != bank_party for offer in visible_offers):
            raise RuntimeError(f"Privacy failure: {bank} can see another owner's offer")
        if {
            requirement.payload["requirementId"]
            for requirement in visible_requirements
        } != expected_requirements:
            raise RuntimeError(
                f"{bank} does not have exactly its expected requirement view"
            )
        if any(
            requirement.payload["obligor"] != bank_party
            for requirement in visible_requirements
        ):
            raise RuntimeError(
                f"Privacy failure: {bank} can see another obligor's requirement"
            )

    allocator_offers = contracts_of_type(
        contracts["Allocator"], "CollateralOffer"
    )
    allocator_requirements = contracts_of_type(
        contracts["Allocator"], "CollateralRequirement"
    )
    if {offer.payload["assetId"] for offer in allocator_offers} != expected_all_assets:
        raise RuntimeError("Allocator cannot see the complete authorised offer set")
    if {
        requirement.payload["requirementId"]
        for requirement in allocator_requirements
    } != expected_all_requirements:
        raise RuntimeError("Allocator cannot see the complete authorised requirement set")

    return {hint: contract_view(rows) for hint, rows in contracts.items()}


def assert_no_cross_party_state(
    bank: str,
    bank_party: str,
    contracts: list[ActiveContract],
) -> None:
    for contract in contracts:
        if contract.template_name in {
            "CollateralOffer",
            "ReallocationProposal",
            "CollateralAllocation",
        } and contract.payload.get("owner") != bank_party:
            raise RuntimeError(
                f"Privacy failure: {bank} can see another owner's {contract.template_name}"
            )
        if (
            contract.template_name == "CollateralRequirement"
            and contract.payload.get("obligor") != bank_party
        ):
            raise RuntimeError(
                f"Privacy failure: {bank} can see another obligor's requirement"
            )


def validate_accepted_state(
    allocator_contracts: list[ActiveContract],
    accepted_proposals: list[ActiveContract],
    offers_by_asset: dict[str, ActiveContract],
    requirements_by_id: dict[str, ActiveContract],
) -> tuple[list[ActiveContract], list[dict[str, str]]]:
    active_ids = {contract.contract_id for contract in allocator_contracts}
    active_offers = contracts_of_type(allocator_contracts, "CollateralOffer")
    active_allocations = contracts_of_type(
        allocator_contracts, "CollateralAllocation"
    )
    accepted_allocations: list[ActiveContract] = []
    residual_summaries: list[dict[str, str]] = []

    for proposal in accepted_proposals:
        original_offer = offers_by_asset[proposal.payload["assetId"]]
        requirement = requirements_by_id[proposal.payload["requirementId"]]
        if proposal.contract_id in active_ids:
            raise RuntimeError("Accepted proposal is still active")
        if original_offer.contract_id in active_ids:
            raise RuntimeError("Accepted proposal's original offer is still active")

        proposed_quantity = Decimal(proposal.payload["quantity"])
        matches = [
            allocation
            for allocation in active_allocations
            if allocation.payload["assetId"] == proposal.payload["assetId"]
            and allocation.payload["requirementId"]
            == proposal.payload["requirementId"]
            and allocation.payload["owner"] == proposal.payload["owner"]
            and allocation.payload["allocator"] == proposal.payload["allocator"]
            and Decimal(allocation.payload["quantity"]) == proposed_quantity
        ]
        if len(matches) != 1:
            raise RuntimeError(
                "Acceptance did not create the exact allocation authorised by the proposal"
            )
        allocation = matches[0]
        if allocation.payload["beneficiary"] != requirement.payload["beneficiary"]:
            raise RuntimeError("Allocation beneficiary does not match the requirement")
        if allocation.payload["location"] != original_offer.payload["location"]:
            raise RuntimeError("Allocation location does not match the consumed offer")

        expected_effective_value = (
            proposed_quantity
            * Decimal(original_offer.payload["marketValue"])
            * (Decimal("1") - Decimal(original_offer.payload["haircut"]))
        )
        actual_effective_value = Decimal(allocation.payload["effectiveValue"])
        if abs(actual_effective_value - expected_effective_value) > DECIMAL_QUANTUM:
            raise RuntimeError("Allocation effective value was calculated incorrectly")
        accepted_allocations.append(allocation)

        expected_remaining = (
            Decimal(original_offer.payload["availableQuantity"])
            - proposed_quantity
        )
        residual_matches = [
            offer
            for offer in active_offers
            if offer.payload["assetId"] == original_offer.payload["assetId"]
            and offer.payload["owner"] == original_offer.payload["owner"]
        ]
        if expected_remaining > 0:
            if len(residual_matches) != 1:
                raise RuntimeError("Acceptance did not create exactly one residual offer")
            residual = residual_matches[0]
            if Decimal(residual.payload["availableQuantity"]) != expected_remaining:
                raise RuntimeError("Residual offer has the wrong available quantity")
            unchanged_fields = (
                "owner",
                "allocator",
                "assetId",
                "assetClass",
                "marketValue",
                "haircut",
                "opportunityCost",
                "location",
            )
            if any(
                residual.payload[field] != original_offer.payload[field]
                for field in unchanged_fields
            ):
                raise RuntimeError("Residual offer changed an economic or ownership field")
            residual_summaries.append(
                {
                    "asset_id": residual.payload["assetId"],
                    "available_quantity": residual.payload["availableQuantity"],
                }
            )
        elif residual_matches:
            raise RuntimeError("A fully consumed offer unexpectedly has a residual")

    return accepted_allocations, residual_summaries


def run_demo(base_url: str, market_path: Path) -> dict[str, Any]:
    client = CantonClient(base_url)
    client.ledger_end()
    with market_path.open(encoding="utf-8") as handle:
        sample = json.load(handle)

    parties = ensure_demo_parties(client)
    allocator_initial = client.active_contracts(parties["Allocator"])
    if contracts_of_type(allocator_initial, "ReallocationProposal") or contracts_of_type(
        allocator_initial, "CollateralAllocation"
    ):
        raise RuntimeError(
            "This demo requires a fresh Sandbox: active proposals or allocations already exist"
        )

    seed_market(client, parties, sample)
    before = assert_initial_authorised_views(client, parties, sample)

    market, offers_by_asset, requirements_by_id = read_allocator_market(
        client, parties["Allocator"]
    )
    result = optimize_collateral(market)
    if result["status"] != "OPTIMAL":
        raise RuntimeError(f"Optimiser did not find a solution: {result}")

    proposals = create_proposals(
        client,
        parties,
        result,
        offers_by_asset,
        requirements_by_id,
    )
    proposal_views = {
        "BankA": party_view(client, parties["BankA"]),
        "BankB": party_view(client, parties["BankB"]),
    }
    for bank in ("BankA", "BankB"):
        expected_assets = sorted(
            allocation["asset_id"]
            for allocation in result["allocations"]
            if allocation["owner"] == bank
        )
        actual_assets = proposal_views[bank].get("ReallocationProposal", [])
        if actual_assets != expected_assets:
            raise RuntimeError(
                f"{bank} proposal visibility is not the expected private view"
            )

    accepted_proposals = [
        proposal
        for proposal in proposals
        if proposal.payload["owner"] == parties["BankA"]
    ]
    if not accepted_proposals:
        raise RuntimeError("No BankA proposal was created")
    for proposal in accepted_proposals:
        client.exercise(
            act_as=parties["BankA"],
            template_id=template_id("ReallocationProposal"),
            contract_id=proposal.contract_id,
            choice="Accept",
            label=f"bank-a-accept-{proposal.payload['assetId'].lower()}",
        )

    allocator_after_contracts = client.active_contracts(parties["Allocator"])
    bank_a_allocations, residual_offers = validate_accepted_state(
        allocator_after_contracts,
        accepted_proposals,
        offers_by_asset,
        requirements_by_id,
    )

    after_contracts = {
        hint: client.active_contracts(party)
        for hint, party in parties.items()
        if hint in {"BankA", "BankB", "Allocator", "CCP"}
    }
    assert_no_cross_party_state(
        "BankA", parties["BankA"], after_contracts["BankA"]
    )
    assert_no_cross_party_state(
        "BankB", parties["BankB"], after_contracts["BankB"]
    )
    ccp_private_types = {
        contract.template_name for contract in after_contracts["CCP"]
    } & {"CollateralOffer", "ReallocationProposal"}
    if ccp_private_types:
        raise RuntimeError("Privacy failure: CCP can see a private offer or proposal")

    accepted_allocation_ids = {
        allocation.contract_id for allocation in bank_a_allocations
    }
    bank_b_contract_ids = {
        contract.contract_id for contract in after_contracts["BankB"]
    }
    bank_a_contract_ids = {
        contract.contract_id for contract in after_contracts["BankA"]
    }
    ccp_contract_ids = {
        contract.contract_id for contract in after_contracts["CCP"]
    }
    if accepted_allocation_ids & bank_b_contract_ids:
        raise RuntimeError("Privacy failure: BankB can see BankA's allocation")
    if not accepted_allocation_ids <= bank_a_contract_ids:
        raise RuntimeError("BankA cannot see its accepted allocation")
    if not accepted_allocation_ids <= ccp_contract_ids:
        raise RuntimeError("CCP cannot see the accepted allocation")

    after = {
        hint: contract_view(contracts)
        for hint, contracts in after_contracts.items()
    }

    return {
        "status": "DEMO_COMPLETE",
        "canton_ledger_end": client.ledger_end(),
        "topology": "Canton Sandbox: one participant and one local synchronizer",
        "privacy_scope": "Daml party-level stakeholder visibility",
        "parties": {hint: party for hint, party in parties.items()},
        "before_optimisation": before,
        "optimisation": result,
        "private_proposals": proposal_views,
        "bank_a_accepted": [
            {
                "asset_id": allocation.payload["assetId"],
                "requirement_id": allocation.payload["requirementId"],
                "quantity": allocation.payload["quantity"],
                "effective_value": allocation.payload["effectiveValue"],
            }
            for allocation in bank_a_allocations
        ],
        "bank_a_residual_offers": residual_offers,
        "after_acceptance": after,
    }


def main() -> None:
    project_root = Path(__file__).parents[1]
    parser = argparse.ArgumentParser(
        description="Run the privacy-preserving collateral demo on Canton"
    )
    parser.add_argument("--base-url", default="http://localhost:7575")
    parser.add_argument(
        "--market",
        type=Path,
        default=project_root / "sample_data" / "market.json",
    )
    args = parser.parse_args()

    try:
        summary = run_demo(args.base_url, args.market)
    except (LedgerApiError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"DEMO FAILED: {exc}") from exc
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
