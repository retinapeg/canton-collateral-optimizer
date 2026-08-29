"""Run the deterministic optimiser result through real bilateral Daml contracts.

The optimiser is ledger-independent. This module is the explicit adapter
between its asset/destination identifiers and the parties, assets, quantities,
and references used by the local Canton demonstration.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from math import isclose
from typing import Any

from backend.canton import ActiveContract, CantonClient, LedgerApiError
from optimizer import AllocationResult, optimize_allocation


PACKAGE_NAME = "collateral-optimizer"
MODULE_NAME = "CollateralAllocation"
REFERENCE_PREFIX = "optimizer-allocation-"
LOCAL_OBJECTIVE_SCORE = 101.0
TRANSACTION_RECEIPT_SEPARATOR = "=" * 60

OPTIMIZER_SUPPLIES = {"Asset1": 1.0, "Asset2": 1.0}
OPTIMIZER_DEMANDS = {"InstitutionB": 1.0, "InstitutionC": 1.0}
OPTIMIZER_COSTS = {
    "Asset1": {"InstitutionB": 1.0, "InstitutionC": 2.0},
    "Asset2": {"InstitutionB": 1.1, "InstitutionC": 100.0},
}
OPTIMIZER_HAIRCUTS = {
    asset_id: {destination_id: 0.0 for destination_id in OPTIMIZER_DEMANDS}
    for asset_id in OPTIMIZER_SUPPLIES
}
OPTIMIZER_ELIGIBILITY = {
    asset_id: {destination_id: True for destination_id in OPTIMIZER_DEMANDS}
    for asset_id in OPTIMIZER_SUPPLIES
}


@dataclass(frozen=True)
class LedgerMapping:
    """Authoritative demo mapping for one optimiser allocation pair."""

    ledger_asset: str
    source_hint: str
    recipient_hint: str
    quantity: Decimal


LEDGER_MAPPING_BY_OPTIMIZER_PAIR = {
    ("Asset2", "InstitutionB"): LedgerMapping(
        ledger_asset="US_TREASURY_2034",
        source_hint="BankA",
        recipient_hint="BankB",
        quantity=Decimal("30"),
    ),
    ("Asset1", "InstitutionC"): LedgerMapping(
        ledger_asset="UK_GILT_2035",
        source_hint="BankA",
        recipient_hint="BankC",
        quantity=Decimal("20"),
    ),
}


@dataclass(frozen=True)
class AllocationInstruction:
    """One optimiser allocation translated into one ledger instruction."""

    sequence: int
    optimizer_asset: str
    optimizer_destination: str
    optimizer_quantity: float
    ledger_asset: str
    source_hint: str
    recipient_hint: str
    quantity: Decimal
    reference: str

    @property
    def quantity_wire(self) -> str:
        return format(self.quantity.quantize(Decimal("0.0000000001")), "f")


@dataclass(frozen=True)
class LedgerFlowResult:
    """Verified active ledger state produced by all optimiser instructions."""

    source_party: str
    recipient_parties: dict[str, str]
    allocations: tuple[ActiveContract, ...]
    authorization_rejected: bool
    create_responses: dict[str, Any]
    accept_responses: dict[str, Any]


def template_id(name: str) -> str:
    return f"#{PACKAGE_NAME}:{MODULE_NAME}:{name}"


def party_display(hint: str) -> str:
    names = {"BankA": "Bank A", "BankB": "Bank B", "BankC": "Bank C"}
    try:
        return names[hint]
    except KeyError as exc:
        raise ValueError(f"No display name is defined for Canton party {hint!r}") from exc


def decimal_display(value: Decimal) -> str:
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def transaction_from_submit_response(response: Any) -> dict[str, Any]:
    """Return the real Canton transaction or reject a malformed response."""

    if not isinstance(response, dict):
        raise LedgerApiError("Canton submit response is not a JSON object")
    transaction = response.get("transaction")
    if not isinstance(transaction, dict):
        raise LedgerApiError("Canton submit response is missing transaction")
    update_id = transaction.get("updateId")
    if not isinstance(update_id, str) or not update_id:
        raise LedgerApiError(
            "Canton transaction response is missing transaction.updateId"
        )
    return transaction


def created_contract_ids(transaction: dict[str, Any]) -> tuple[str, ...]:
    """Extract only contract IDs supplied by Canton created events."""

    events = transaction.get("events")
    event_rows = events if isinstance(events, list) else []
    if not event_rows:
        events_by_id = transaction.get("eventsById")
        if isinstance(events_by_id, dict):
            event_rows = list(events_by_id.values())

    contract_ids: list[str] = []
    for event in event_rows:
        if not isinstance(event, dict):
            continue
        created = event.get("CreatedEvent")
        if not isinstance(created, dict):
            tree_created = event.get("CreatedTreeEvent")
            if isinstance(tree_created, dict):
                value = tree_created.get("value")
                created = value if isinstance(value, dict) else tree_created
        if not isinstance(created, dict):
            continue
        contract_id = created.get("contractId")
        if isinstance(contract_id, str) and contract_id:
            contract_ids.append(contract_id)
    return tuple(contract_ids)


def format_transaction_receipt(
    response: Any,
    *,
    source: str,
    recipient: str,
    action: str,
) -> str:
    """Format a committed receipt using only fields Canton returned."""

    transaction = transaction_from_submit_response(response)
    lines = [
        TRANSACTION_RECEIPT_SEPARATOR,
        "✅ CANTON / DAML TRANSACTION COMMITTED",
        f"FROM: {source}",
        f"TO: {recipient}",
        f"ACTION: {action}",
        f"UPDATE ID (TXID equivalent): {transaction['updateId']}",
    ]
    command_id = transaction.get("commandId")
    if isinstance(command_id, str) and command_id:
        lines.append(f"COMMAND ID: {command_id}")
    offset = transaction.get("offset")
    if offset is not None:
        lines.append(f"LEDGER OFFSET: {offset}")
    lines.extend(
        f"CREATED CONTRACT ID: {contract_id}"
        for contract_id in created_contract_ids(transaction)
    )
    lines.extend(["STATUS: COMMITTED", TRANSACTION_RECEIPT_SEPARATOR])
    return "\n".join(lines)


def run_optimizer() -> AllocationResult:
    result = optimize_allocation(
        supplies=OPTIMIZER_SUPPLIES,
        demands=OPTIMIZER_DEMANDS,
        costs=OPTIMIZER_COSTS,
        haircuts=OPTIMIZER_HAIRCUTS,
        eligibility=OPTIMIZER_ELIGIBILITY,
    )
    if not result.success or result.total_cost is None:
        raise RuntimeError(f"Optimizer failed: {result.status} - {result.message}")
    if not isclose(result.total_cost, 3.1, abs_tol=1e-9):
        raise RuntimeError(
            f"Expected global objective score 3.1, got {result.total_cost!r}"
        )
    return result


def optimizer_allocation_pairs(
    result: AllocationResult,
) -> list[tuple[str, str, float]]:
    """Extract positive optimiser allocations in destination display order."""

    pairs: list[tuple[str, str, float]] = []
    for destination_id in OPTIMIZER_DEMANDS:
        for asset_id in OPTIMIZER_SUPPLIES:
            quantity = result.allocations[asset_id][destination_id]
            if quantity > 0.0:
                pairs.append((asset_id, destination_id, quantity))
    return pairs


def build_ledger_instructions(
    result: AllocationResult,
) -> tuple[AllocationInstruction, ...]:
    """Map every positive optimiser result; fail if any result is unmapped."""

    pairs = optimizer_allocation_pairs(result)
    instructions: list[AllocationInstruction] = []
    for sequence, (asset_id, destination_id, optimizer_quantity) in enumerate(
        pairs, start=1
    ):
        mapping = LEDGER_MAPPING_BY_OPTIMIZER_PAIR.get((asset_id, destination_id))
        if mapping is None:
            raise RuntimeError(
                "Optimizer produced an allocation with no ledger mapping: "
                f"{asset_id} -> {destination_id}"
            )
        instructions.append(
            AllocationInstruction(
                sequence=sequence,
                optimizer_asset=asset_id,
                optimizer_destination=destination_id,
                optimizer_quantity=optimizer_quantity,
                ledger_asset=mapping.ledger_asset,
                source_hint=mapping.source_hint,
                recipient_hint=mapping.recipient_hint,
                quantity=mapping.quantity,
                reference=f"{REFERENCE_PREFIX}{sequence:03d}",
            )
        )

    expected_pairs = list(LEDGER_MAPPING_BY_OPTIMIZER_PAIR)
    actual_pairs = [
        (instruction.optimizer_asset, instruction.optimizer_destination)
        for instruction in instructions
    ]
    if actual_pairs != expected_pairs:
        raise RuntimeError(
            "Optimizer allocations do not match the authoritative demo mapping: "
            f"got {actual_pairs!r}, expected {expected_pairs!r}"
        )
    return tuple(instructions)


def find_one(
    contracts: Sequence[ActiveContract], template_name: str, reference: str
) -> ActiveContract:
    matches = [
        contract
        for contract in contracts
        if contract.template_name == template_name
        and contract.payload.get("allocationReference") == reference
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one {template_name} for {reference}, "
            f"found {len(matches)}"
        )
    return matches[0]


def is_demo_contract(contract: ActiveContract) -> bool:
    reference = contract.payload.get("allocationReference")
    return (
        contract.template_name in {"AllocationProposal", "AllocatedCollateral"}
        and isinstance(reference, str)
        and reference.startswith(REFERENCE_PREFIX)
    )


def expected_payload(
    instruction: AllocationInstruction, *, source: str, recipient: str
) -> dict[str, Any]:
    return {
        "source": source,
        "recipient": recipient,
        "asset": instruction.ledger_asset,
        "quantity": instruction.quantity_wire,
        "allocationReference": instruction.reference,
    }


def validate_proposal(
    proposal: ActiveContract,
    instruction: AllocationInstruction,
    *,
    source: str,
    recipient: str,
) -> None:
    expected = expected_payload(instruction, source=source, recipient=recipient)
    if proposal.payload != expected:
        raise RuntimeError(
            f"Proposal {instruction.reference} does not match its optimiser "
            f"instruction: {proposal.payload!r}"
        )
    if set(proposal.signatories) != {source}:
        raise RuntimeError(
            f"Proposal {instruction.reference} does not have Bank A as signatory"
        )
    if recipient not in proposal.observers:
        raise RuntimeError(
            f"Proposal {instruction.reference} is not observed by its recipient"
        )


def validate_allocation(
    allocation: ActiveContract,
    instruction: AllocationInstruction,
    *,
    source: str,
    recipient: str,
) -> None:
    expected = expected_payload(instruction, source=source, recipient=recipient)
    if allocation.payload != expected:
        raise RuntimeError(
            f"Accepted allocation {instruction.reference} does not match its "
            f"optimiser instruction: {allocation.payload!r}"
        )
    if set(allocation.signatories) != {source, recipient}:
        raise RuntimeError(
            f"Accepted allocation {instruction.reference} does not have both "
            "bank signatories"
        )


def is_recipient_authorization_rejection(error: LedgerApiError) -> bool:
    """Distinguish a Canton authorization rejection from transport failures."""

    detail = str(error).lower()
    markers = (
        "daml_authorization_error",
        "requires authorizers",
        "requires authorizer",
        "missing authorization",
        "no_authoriz",
    )
    return "canton returned http 4" in detail and any(
        marker in detail for marker in markers
    )


def run_ledger_flow(
    client: CantonClient,
    instructions: Sequence[AllocationInstruction],
    *,
    emit: Callable[[str], None] = print,
) -> LedgerFlowResult:
    """Create, reject an unauthorized choice, accept, query, and reconcile."""

    if not instructions:
        raise RuntimeError("Optimizer produced no ledger instructions")
    source_hints = {instruction.source_hint for instruction in instructions}
    if source_hints != {"BankA"}:
        raise RuntimeError(f"Expected BankA as the sole source, got {source_hints!r}")

    client.ledger_end()
    source = client.ensure_party("BankA")
    recipient_parties = {
        instruction.recipient_hint: client.ensure_party(instruction.recipient_hint)
        for instruction in instructions
    }

    existing = [
        contract
        for contract in client.active_contracts(source)
        if is_demo_contract(contract)
    ]
    if existing:
        raise RuntimeError(
            "This deterministic demo requires a fresh local Canton ledger without "
            f"active {REFERENCE_PREFIX}* contracts"
        )

    authorization_rejected = False
    accepted_by_reference: dict[str, ActiveContract] = {}
    create_responses: dict[str, Any] = {}
    accept_responses: dict[str, Any] = {}

    for instruction in instructions:
        recipient = recipient_parties[instruction.recipient_hint]
        payload = expected_payload(instruction, source=source, recipient=recipient)

        emit(f"\nCreating {instruction.reference}...")
        create_response = client.create(
            act_as=source,
            template_id=template_id("AllocationProposal"),
            arguments=payload,
            label=f"bank-a-propose-{instruction.reference}",
        )
        create_responses[instruction.reference] = create_response
        if instruction.recipient_hint == "BankB":
            emit(
                format_transaction_receipt(
                    create_response,
                    source=party_display(instruction.source_hint),
                    recipient=party_display(instruction.recipient_hint),
                    action=template_id("AllocationProposal"),
                )
            )

        proposal = find_one(
            client.active_contracts(recipient),
            "AllocationProposal",
            instruction.reference,
        )
        validate_proposal(
            proposal, instruction, source=source, recipient=recipient
        )
        emit("\n✓ DAML SMART CONTRACT CREATED")
        emit("  Template: AllocationProposal")
        emit(
            f"  {party_display(instruction.source_hint)} -> "
            f"{party_display(instruction.recipient_hint)}"
        )
        emit(f"  Asset: {instruction.ledger_asset}")
        emit(f"  Quantity: {decimal_display(instruction.quantity)}")

        if instruction.recipient_hint == "BankB":
            try:
                client.exercise(
                    act_as=source,
                    template_id=template_id("AllocationProposal"),
                    contract_id=proposal.contract_id,
                    choice="Accept",
                    label="bank-a-unauthorized-bank-b-accept",
                )
            except LedgerApiError as exc:
                if not is_recipient_authorization_rejection(exc):
                    raise
                authorization_rejected = True
            else:
                raise RuntimeError(
                    "Canton allowed Bank A to exercise Bank B's Accept choice"
                )

            # A rejected transaction must not consume or mutate the proposal.
            proposal = find_one(
                client.active_contracts(recipient),
                "AllocationProposal",
                instruction.reference,
            )
            validate_proposal(
                proposal, instruction, source=source, recipient=recipient
            )

        emit(
            f"\n{party_display(instruction.recipient_hint)} exercising Accept..."
        )
        accept_response = client.exercise(
            act_as=recipient,
            template_id=template_id("AllocationProposal"),
            contract_id=proposal.contract_id,
            choice="Accept",
            label=(
                f"{instruction.recipient_hint.lower()}-accept-"
                f"{instruction.reference}"
            ),
        )
        accept_responses[instruction.reference] = accept_response
        if instruction.recipient_hint == "BankB":
            emit(
                format_transaction_receipt(
                    accept_response,
                    source=party_display(instruction.source_hint),
                    recipient=party_display(instruction.recipient_hint),
                    action=f"{template_id('AllocationProposal')}.Accept",
                )
            )

        recipient_view = client.active_contracts(recipient)
        if any(
            contract.contract_id == proposal.contract_id
            for contract in recipient_view
        ):
            raise RuntimeError(
                f"Accepted proposal {instruction.reference} is still active"
            )
        allocation = find_one(
            recipient_view, "AllocatedCollateral", instruction.reference
        )
        validate_allocation(
            allocation, instruction, source=source, recipient=recipient
        )
        accepted_by_reference[instruction.reference] = allocation
        emit("\n✓ DAML CHOICE EXERCISED")
        emit("✓ Accepted allocation contract created")

    if not authorization_rejected:
        raise RuntimeError("Bank A authorization rejection was not observed")

    source_view = [
        contract
        for contract in client.active_contracts(source)
        if is_demo_contract(contract)
    ]
    remaining_proposals = [
        contract
        for contract in source_view
        if contract.template_name == "AllocationProposal"
    ]
    if remaining_proposals:
        references = [
            contract.payload.get("allocationReference")
            for contract in remaining_proposals
        ]
        raise RuntimeError(f"Accepted proposals remain active: {references!r}")

    allocations = [
        contract
        for contract in source_view
        if contract.template_name == "AllocatedCollateral"
    ]
    expected_references = {instruction.reference for instruction in instructions}
    actual_references = {
        contract.payload.get("allocationReference") for contract in allocations
    }
    if len(allocations) != len(instructions) or actual_references != expected_references:
        raise RuntimeError(
            "Unexpected accepted allocations for demo reference prefix: "
            f"count={len(allocations)}, references={actual_references!r}"
        )

    instruction_by_reference = {
        instruction.reference: instruction for instruction in instructions
    }
    for allocation in allocations:
        reference = allocation.payload["allocationReference"]
        instruction = instruction_by_reference[reference]
        recipient = recipient_parties[instruction.recipient_hint]
        validate_allocation(
            allocation, instruction, source=source, recipient=recipient
        )
        if allocation.contract_id != accepted_by_reference[reference].contract_id:
            raise RuntimeError(
                f"Source and recipient views disagree for {reference}"
            )

    for instruction in instructions:
        recipient = recipient_parties[instruction.recipient_hint]
        recipient_demo_state = [
            contract
            for contract in client.active_contracts(recipient)
            if is_demo_contract(contract)
        ]
        recipient_proposals = [
            contract
            for contract in recipient_demo_state
            if contract.template_name == "AllocationProposal"
        ]
        recipient_allocations = [
            contract
            for contract in recipient_demo_state
            if contract.template_name == "AllocatedCollateral"
        ]
        if recipient_proposals:
            raise RuntimeError(
                f"{instruction.recipient_hint} still sees an active demo proposal"
            )
        if (
            len(recipient_allocations) != 1
            or recipient_allocations[0].payload.get("allocationReference")
            != instruction.reference
            or recipient_allocations[0].contract_id
            != accepted_by_reference[instruction.reference].contract_id
        ):
            raise RuntimeError(
                f"{instruction.recipient_hint} does not see exactly its own "
                "accepted optimizer allocation"
            )

    ordered_allocations = tuple(
        accepted_by_reference[instruction.reference] for instruction in instructions
    )
    return LedgerFlowResult(
        source_party=source,
        recipient_parties=recipient_parties,
        allocations=ordered_allocations,
        authorization_rejected=authorization_rejected,
        create_responses=create_responses,
        accept_responses=accept_responses,
    )


def reconcile(
    instructions: Sequence[AllocationInstruction], flow: LedgerFlowResult
) -> None:
    """Assert exact per-bank quantities and one contract per instruction."""

    allocation_by_reference = {
        allocation.payload["allocationReference"]: allocation
        for allocation in flow.allocations
    }
    if len(allocation_by_reference) != len(flow.allocations):
        raise RuntimeError("Accepted allocation references are not unique")
    if len(flow.allocations) != len(instructions):
        raise RuntimeError(
            "Committed allocation count does not equal optimiser instruction count"
        )

    for instruction in instructions:
        try:
            allocation = allocation_by_reference[instruction.reference]
        except KeyError as exc:
            raise RuntimeError(
                f"No accepted allocation exists for {instruction.reference}"
            ) from exc
        recipient = flow.recipient_parties[instruction.recipient_hint]
        validate_allocation(
            allocation,
            instruction,
            source=flow.source_party,
            recipient=recipient,
        )
        if Decimal(allocation.payload["quantity"]) != instruction.quantity:
            raise RuntimeError(
                f"Wrong reconciled quantity for {instruction.reference}"
            )


def print_optimizer_section(
    result: AllocationResult, instructions: Sequence[AllocationInstruction]
) -> None:
    improvement = LOCAL_OBJECTIVE_SCORE - float(result.total_cost)
    reduction = improvement / LOCAL_OBJECTIVE_SCORE * 100.0
    print("============================================================")
    print("CANTON GLOBAL COLLATERAL OPTIMIZER")
    print("============================================================")
    print("\n[1] GLOBAL OPTIMIZER\n")
    print(f"Local / greedy objective score: {LOCAL_OBJECTIVE_SCORE:.1f}")
    print(f"Global optimum objective score: {result.total_cost:.1f}")
    print(f"Improvement: {improvement:.1f}")
    print(f"Reduction: approximately {reduction:.1f}%")
    print(
        "\nThe optimiser minimizes a normalized allocation cost derived from the "
        "eligibility/cost matrix. Lower is better. In this illustrative scenario "
        f"the score falls from {LOCAL_OBJECTIVE_SCORE:.1f} to "
        f"{result.total_cost:.1f}, a reduction of approximately {reduction:.1f}%."
    )
    print("\nOptimal allocation:\n")
    for instruction in instructions:
        print(
            f"{instruction.optimizer_asset} -> "
            f"{instruction.optimizer_destination}"
        )
    print("\n✓ OPTIMIZER PASS")


def print_mapping_section(instructions: Sequence[AllocationInstruction]) -> None:
    print("\n\n[2] OPTIMIZER -> LEDGER MAPPING")
    for instruction in instructions:
        print(f"\nInstruction {instruction.sequence}")
        print(f"  Optimizer asset: {instruction.optimizer_asset}")
        print(f"  Ledger asset: {instruction.ledger_asset}")
        print(
            f"  Optimizer destination: {instruction.optimizer_destination}"
        )
        print(
            f"  Canton party: {party_display(instruction.recipient_hint)}"
        )
        print(f"  Quantity: {decimal_display(instruction.quantity)}")


def print_ledger_section(
    instructions: Sequence[AllocationInstruction], flow: LedgerFlowResult
) -> None:
    print("\n\n[4] CANTON LEDGER\n")
    print("✓ DAML SMART CONTRACTS COMMITTED TO CANTON")
    print("\nLedger query:")
    for instruction, allocation in zip(instructions, flow.allocations, strict=True):
        print(f"\n{instruction.reference}")
        print(f"  Source: {party_display(instruction.source_hint)}")
        print(f"  Recipient: {party_display(instruction.recipient_hint)}")
        print(f"  Asset: {allocation.payload['asset']}")
        print(
            f"  Quantity: {decimal_display(Decimal(allocation.payload['quantity']))}"
        )
        print("  Status: ACTIVE")


def print_reconciliation_section(
    instructions: Sequence[AllocationInstruction], flow: LedgerFlowResult
) -> None:
    print("\n\n[5] RECONCILIATION")
    for instruction in instructions:
        bank = party_display(instruction.recipient_hint)
        quantity = decimal_display(instruction.quantity)
        print(f"\n{bank} required: {quantity}")
        print(f"{bank} received: {quantity} {instruction.ledger_asset}")
        print("✓ PASS")
    print(f"\nOptimizer instructions generated: {len(instructions)}")
    print(
        "Optimizer instructions committed to Canton: "
        f"{len(flow.allocations)}"
    )
    print("✓ PASS")
    print("\n✓ OPTIMIZER-TO-LEDGER END-TO-END PASS")


def print_authorization_section(flow: LedgerFlowResult) -> None:
    if not flow.authorization_rejected:
        raise RuntimeError("Recipient authority was not enforced")
    print("\n\n[6] AUTHORIZATION\n")
    print("Bank A attempts to exercise Bank B's Accept choice...")
    print("\n✗ REJECTED BY CANTON")
    print("\n✓ recipient authority enforced")


def print_footer() -> None:
    print("\n\n============================================================")
    print("DEMO PASS")
    print("GLOBAL OPTIMIZER -> DAML SMART CONTRACTS -> CANTON LEDGER")
    print("============================================================")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run every global optimiser allocation on local Canton"
    )
    parser.add_argument("--base-url", default="http://localhost:7575")
    args = parser.parse_args()

    try:
        result = run_optimizer()
        instructions = build_ledger_instructions(result)
        print_optimizer_section(result, instructions)
        print_mapping_section(instructions)
        print("\n\n[3] DAML SMART CONTRACTS")
        flow = run_ledger_flow(CantonClient(args.base_url), instructions)
        reconcile(instructions, flow)
        print_ledger_section(instructions, flow)
        print_reconciliation_section(instructions, flow)
        print_authorization_section(flow)
        print_footer()
    except (LedgerApiError, RuntimeError, ValueError) as exc:
        raise SystemExit(f"ALLOCATION DEMO FAILED: {exc}") from exc


if __name__ == "__main__":
    main()
