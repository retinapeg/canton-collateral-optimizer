from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import unittest

from backend.allocation_demo import (
    AllocationInstruction,
    build_ledger_instructions,
    created_contract_ids,
    format_transaction_receipt,
    reconcile,
    run_ledger_flow,
    run_optimizer,
)
from backend.canton import ActiveContract, LedgerApiError
from optimizer import AllocationResult


class FakeCantonClient:
    """Stateful party-visible ledger used to test the adapter orchestration."""

    def __init__(
        self,
        *,
        allow_bank_a_accept: bool = False,
        unauthorized_error: str | None = None,
        add_unexpected_allocation: bool = False,
    ) -> None:
        self.parties = {
            "BankA": "BankA::test",
            "BankB": "BankB::test",
            "BankC": "BankC::test",
        }
        self.allow_bank_a_accept = allow_bank_a_accept
        self.unauthorized_error = unauthorized_error
        self.add_unexpected_allocation = add_unexpected_allocation
        self.contracts: dict[str, ActiveContract] = {}
        self.create_calls: list[dict[str, object]] = []
        self.exercise_calls: list[dict[str, object]] = []
        self._next_contract = 1

    def ledger_end(self) -> int:
        return 0

    def ensure_party(self, hint: str) -> str:
        return self.parties[hint]

    def active_contracts(self, party: str) -> list[ActiveContract]:
        return [
            contract
            for contract in self.contracts.values()
            if party in {*contract.signatories, *contract.observers}
        ]

    def create(
        self,
        *,
        act_as: str,
        template_id: str,
        arguments: dict[str, object],
        label: str,
    ) -> dict[str, object]:
        self.create_calls.append(
            {
                "act_as": act_as,
                "template_id": template_id,
                "arguments": arguments,
                "label": label,
            }
        )
        contract_id = f"proposal-{self._next_contract}"
        self._next_contract += 1
        self.contracts[contract_id] = ActiveContract(
            contract_id=contract_id,
            template_id="package:CollateralAllocation:AllocationProposal",
            payload=dict(arguments),
            witness_parties=(
                str(arguments["source"]),
                str(arguments["recipient"]),
            ),
            signatories=(str(arguments["source"]),),
            observers=(str(arguments["recipient"]),),
        )
        return {
            "transaction": {
                "updateId": f"create-update-{contract_id}",
                "events": [{"CreatedEvent": {"contractId": contract_id}}],
            }
        }

    def exercise(
        self,
        *,
        act_as: str,
        template_id: str,
        contract_id: str,
        choice: str,
        choice_argument: dict[str, object] | None = None,
        label: str,
    ) -> dict[str, object]:
        self.exercise_calls.append(
            {
                "act_as": act_as,
                "template_id": template_id,
                "contract_id": contract_id,
                "choice": choice,
                "label": label,
            }
        )
        proposal = self.contracts[contract_id]
        recipient = str(proposal.payload["recipient"])
        if act_as != recipient and not self.allow_bank_a_accept:
            message = self.unauthorized_error or (
                "Canton returned HTTP 400: DAML_AUTHORIZATION_ERROR: "
                "exercise requires authorizers from the recipient"
            )
            raise LedgerApiError(message)

        del self.contracts[contract_id]
        allocation_id = f"allocation-{self._next_contract}"
        self._next_contract += 1
        source = str(proposal.payload["source"])
        self.contracts[allocation_id] = ActiveContract(
            contract_id=allocation_id,
            template_id="package:CollateralAllocation:AllocatedCollateral",
            payload=dict(proposal.payload),
            witness_parties=(source, recipient),
            signatories=(source, recipient),
            observers=(),
        )

        if (
            self.add_unexpected_allocation
            and proposal.payload["allocationReference"]
            == "optimizer-allocation-002"
        ):
            unexpected_id = "allocation-unexpected"
            self.contracts[unexpected_id] = ActiveContract(
                contract_id=unexpected_id,
                template_id="package:CollateralAllocation:AllocatedCollateral",
                payload={
                    "source": source,
                    "recipient": recipient,
                    "asset": "UNEXPECTED",
                    "quantity": "1.0000000000",
                    "allocationReference": "optimizer-allocation-003",
                },
                witness_parties=(source, recipient),
                signatories=(source, recipient),
                observers=(),
            )

        return {
            "transaction": {
                "updateId": f"accept-update-{allocation_id}",
                "events": [{"CreatedEvent": {"contractId": allocation_id}}],
            }
        }


class AllocationMappingTests(unittest.TestCase):
    def test_real_optimizer_builds_exact_ordered_ledger_instructions(self) -> None:
        result = run_optimizer()
        instructions = build_ledger_instructions(result)

        self.assertAlmostEqual(result.total_cost, 3.1)
        self.assertAlmostEqual(101.0 - result.total_cost, 97.9)
        self.assertAlmostEqual((101.0 - result.total_cost) / 101.0 * 100, 96.9, 1)
        self.assertEqual(
            [
                (
                    row.optimizer_asset,
                    row.optimizer_destination,
                    row.source_hint,
                    row.recipient_hint,
                    row.ledger_asset,
                    row.quantity,
                    row.reference,
                )
                for row in instructions
            ],
            [
                (
                    "Asset2",
                    "InstitutionB",
                    "BankA",
                    "BankB",
                    "US_TREASURY_2034",
                    Decimal("30"),
                    "optimizer-allocation-001",
                ),
                (
                    "Asset1",
                    "InstitutionC",
                    "BankA",
                    "BankC",
                    "UK_GILT_2035",
                    Decimal("20"),
                    "optimizer-allocation-002",
                ),
            ],
        )
        self.assertEqual(
            [row.quantity_wire for row in instructions],
            ["30.0000000000", "20.0000000000"],
        )

    def test_mapping_rejects_a_missing_optimizer_allocation(self) -> None:
        allocations = {
            "Asset1": {"InstitutionB": 0.0, "InstitutionC": 0.0},
            "Asset2": {"InstitutionB": 1.0, "InstitutionC": 0.0},
        }
        result = AllocationResult(
            success=True,
            total_cost=3.1,
            allocations=allocations,
            status="OPTIMAL",
            message="ok",
        )

        with self.assertRaisesRegex(RuntimeError, "authoritative demo mapping"):
            build_ledger_instructions(result)

    def test_mapping_rejects_an_unmapped_positive_optimizer_allocation(self) -> None:
        allocations = {
            "Asset1": {"InstitutionB": 1.0, "InstitutionC": 1.0},
            "Asset2": {"InstitutionB": 0.0, "InstitutionC": 0.0},
        }
        result = AllocationResult(
            success=True,
            total_cost=3.1,
            allocations=allocations,
            status="OPTIMAL",
            message="ok",
        )

        with self.assertRaisesRegex(RuntimeError, "no ledger mapping"):
            build_ledger_instructions(result)


class AllocationLedgerFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.instructions = build_ledger_instructions(run_optimizer())

    def test_two_proposals_are_accepted_by_their_own_recipients(self) -> None:
        client = FakeCantonClient()
        messages: list[str] = []

        flow = run_ledger_flow(client, self.instructions, emit=messages.append)
        reconcile(self.instructions, flow)

        self.assertTrue(flow.authorization_rejected)
        self.assertEqual(len(flow.allocations), len(self.instructions), 2)
        self.assertEqual(
            set(flow.create_responses),
            {"optimizer-allocation-001", "optimizer-allocation-002"},
        )
        self.assertEqual(
            set(flow.accept_responses),
            {"optimizer-allocation-001", "optimizer-allocation-002"},
        )
        self.assertEqual(len(client.create_calls), 2)
        self.assertEqual(
            [call["arguments"] for call in client.create_calls],
            [
                {
                    "source": "BankA::test",
                    "recipient": "BankB::test",
                    "asset": "US_TREASURY_2034",
                    "quantity": "30.0000000000",
                    "allocationReference": "optimizer-allocation-001",
                },
                {
                    "source": "BankA::test",
                    "recipient": "BankC::test",
                    "asset": "UK_GILT_2035",
                    "quantity": "20.0000000000",
                    "allocationReference": "optimizer-allocation-002",
                },
            ],
        )
        self.assertEqual(
            [call["act_as"] for call in client.exercise_calls],
            ["BankA::test", "BankB::test", "BankC::test"],
        )
        self.assertEqual(
            [contract.payload["allocationReference"] for contract in flow.allocations],
            ["optimizer-allocation-001", "optimizer-allocation-002"],
        )
        receipt_text = "\n".join(
            message
            for message in messages
            if "✅ CANTON / DAML TRANSACTION COMMITTED" in message
        )
        self.assertEqual(
            receipt_text.count("✅ CANTON / DAML TRANSACTION COMMITTED"), 2
        )
        self.assertEqual(receipt_text.count("FROM: Bank A"), 2)
        self.assertEqual(receipt_text.count("TO: Bank B"), 2)
        self.assertNotIn("TO: Bank C", receipt_text)
        self.assertIn(
            "UPDATE ID (TXID equivalent): create-update-proposal-1",
            receipt_text,
        )
        self.assertIn(
            "UPDATE ID (TXID equivalent): accept-update-allocation-2",
            receipt_text,
        )
        self.assertNotIn("create-update-proposal-3", receipt_text)
        self.assertNotIn("accept-update-allocation-4", receipt_text)
        self.assertIn("CREATED CONTRACT ID: proposal-1", receipt_text)
        self.assertIn("CREATED CONTRACT ID: allocation-2", receipt_text)
        self.assertEqual(receipt_text.count("STATUS: COMMITTED"), 2)

    def test_flow_fails_if_bank_a_can_exercise_bank_bs_choice(self) -> None:
        client = FakeCantonClient(allow_bank_a_accept=True)

        with self.assertRaisesRegex(RuntimeError, "allowed Bank A"):
            run_ledger_flow(client, self.instructions, emit=lambda _: None)

    def test_non_authorization_error_is_not_reported_as_enforcement(self) -> None:
        client = FakeCantonClient(
            unauthorized_error="Cannot reach Canton at http://test: connection lost"
        )

        with self.assertRaisesRegex(LedgerApiError, "Cannot reach Canton"):
            run_ledger_flow(client, self.instructions, emit=lambda _: None)

    def test_reconciliation_rejects_an_unexpected_prefix_allocation(self) -> None:
        client = FakeCantonClient(add_unexpected_allocation=True)

        with self.assertRaisesRegex(RuntimeError, "Unexpected accepted allocations"):
            run_ledger_flow(client, self.instructions, emit=lambda _: None)

    def test_reconcile_rejects_a_mutated_quantity(self) -> None:
        client = FakeCantonClient()
        flow = run_ledger_flow(client, self.instructions, emit=lambda _: None)
        first = flow.allocations[0]
        bad_first = replace(
            first,
            payload={**first.payload, "quantity": "29.0000000000"},
        )
        bad_flow = replace(flow, allocations=(bad_first, flow.allocations[1]))

        with self.assertRaisesRegex(RuntimeError, "does not match"):
            reconcile(self.instructions, bad_flow)


class TransactionReceiptTests(unittest.TestCase):
    def test_receipt_prints_exact_canton_fields_and_created_event(self) -> None:
        response = {
            "transaction": {
                "updateId": "actual-update-id-from-canton",
                "commandId": "actual-command-id-from-canton",
                "offset": 42,
                "events": [
                    {"CreatedEvent": {"contractId": "actual-created-contract-id"}}
                ],
            }
        }

        receipt = format_transaction_receipt(
            response,
            source="Bank A",
            recipient="Bank B",
            action="#collateral-optimizer:CollateralAllocation:AllocationProposal",
        )

        self.assertEqual(
            receipt,
            "\n".join(
                [
                    "============================================================",
                    "✅ CANTON / DAML TRANSACTION COMMITTED",
                    "FROM: Bank A",
                    "TO: Bank B",
                    "ACTION: #collateral-optimizer:CollateralAllocation:AllocationProposal",
                    "UPDATE ID (TXID equivalent): actual-update-id-from-canton",
                    "COMMAND ID: actual-command-id-from-canton",
                    "LEDGER OFFSET: 42",
                    "CREATED CONTRACT ID: actual-created-contract-id",
                    "STATUS: COMMITTED",
                    "============================================================",
                ]
            ),
        )

    def test_tree_created_event_is_supported_and_optional_lines_are_omitted(
        self,
    ) -> None:
        response = {
            "transaction": {
                "updateId": "tree-update-id",
                "events": [
                    {
                        "CreatedTreeEvent": {
                            "value": {"contractId": "tree-created-contract-id"}
                        }
                    }
                ],
            }
        }

        receipt = format_transaction_receipt(
            response,
            source="Bank A",
            recipient="Bank B",
            action="AllocationProposal.Accept",
        )

        self.assertIn("UPDATE ID (TXID equivalent): tree-update-id", receipt)
        self.assertIn("CREATED CONTRACT ID: tree-created-contract-id", receipt)
        self.assertNotIn("COMMAND ID:", receipt)
        self.assertNotIn("LEDGER OFFSET:", receipt)

    def test_events_by_id_tree_shape_is_supported(self) -> None:
        transaction = {
            "updateId": "tree-update-id",
            "eventsById": {
                "0": {
                    "CreatedTreeEvent": {
                        "value": {"contractId": "tree-map-contract-id"}
                    }
                }
            },
        }

        self.assertEqual(
            created_contract_ids(transaction), ("tree-map-contract-id",)
        )

    def test_missing_update_id_is_rejected_instead_of_invented(self) -> None:
        with self.assertRaisesRegex(LedgerApiError, "transaction.updateId"):
            format_transaction_receipt(
                {"transaction": {"events": []}},
                source="Bank A",
                recipient="Bank B",
                action="AllocationProposal",
            )


if __name__ == "__main__":
    unittest.main()
