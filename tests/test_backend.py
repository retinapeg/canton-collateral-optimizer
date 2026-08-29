from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.canton import ActiveContract, CantonClient, LedgerApiError
from backend.demo import (
    assert_no_cross_party_state,
    daml_decimal,
    party_hint,
    template_id,
    validate_accepted_state,
)


def contract(
    contract_id: str, template_name: str, payload: dict[str, str]
) -> ActiveContract:
    return ActiveContract(
        contract_id=contract_id,
        template_id=f"package:Collateral:{template_name}",
        payload=payload,
        witness_parties=(),
        signatories=(),
        observers=(),
    )


class BackendMappingTests(unittest.TestCase):
    def test_party_allocation_retries_canton_startup_race(self) -> None:
        client = CantonClient(timeout=1.0)
        allocated = {"partyDetails": {"party": "BankA::namespace"}}
        with (
            patch.object(client, "list_parties", return_value=[]),
            patch.object(
                client,
                "_request",
                side_effect=[
                    LedgerApiError(
                        "PARTY_ALLOCATION_WITHOUT_CONNECTED_SYNCHRONIZER"
                    ),
                    allocated,
                ],
            ) as request,
            patch("backend.canton.sleep"),
        ):
            party = client.ensure_party("BankA")

        self.assertEqual(party, "BankA::namespace")
        self.assertEqual(request.call_count, 2)

    def test_lp_quantity_is_rounded_up_to_daml_decimal_scale(self) -> None:
        self.assertEqual(daml_decimal(500.0 / 98.0, round_up=True), "5.1020408164")

    def test_exact_decimal_is_not_increased(self) -> None:
        self.assertEqual(daml_decimal(5.0, round_up=True), "5.0000000000")

    def test_party_hint_removes_only_the_canton_namespace(self) -> None:
        self.assertEqual(party_hint("BankA::namespace"), "BankA")

    def test_template_id_uses_package_name_reference(self) -> None:
        self.assertEqual(
            template_id("CollateralOffer"),
            "#collateral-optimizer:Collateral:CollateralOffer",
        )

    def test_cross_party_offer_is_rejected_by_privacy_verifier(self) -> None:
        foreign_offer = contract(
            "offer-b",
            "CollateralOffer",
            {"owner": "BankB::namespace", "assetId": "NOT-PREFIX-DEPENDENT"},
        )

        with self.assertRaisesRegex(RuntimeError, "another owner's"):
            assert_no_cross_party_state(
                "BankA", "BankA::namespace", [foreign_offer]
            )

    def test_accepted_state_validates_exact_allocation_and_residual(self) -> None:
        offer_payload = {
            "owner": "BankA::namespace",
            "allocator": "Allocator::namespace",
            "assetId": "A-GILT",
            "assetClass": "GOVERNMENT_BOND",
            "marketValue": "100.0000000000",
            "haircut": "0.0200000000",
            "opportunityCost": "0.5000000000",
            "availableQuantity": "10.0000000000",
            "location": "Custodian-A",
        }
        original_offer = contract("offer-old", "CollateralOffer", offer_payload)
        requirement = contract(
            "requirement",
            "CollateralRequirement",
            {
                "requirementId": "REQ-A",
                "beneficiary": "CCP::namespace",
            },
        )
        proposal = contract(
            "proposal",
            "ReallocationProposal",
            {
                "owner": "BankA::namespace",
                "allocator": "Allocator::namespace",
                "assetId": "A-GILT",
                "requirementId": "REQ-A",
                "quantity": "5.1020408164",
            },
        )
        allocation = contract(
            "allocation",
            "CollateralAllocation",
            {
                "owner": "BankA::namespace",
                "allocator": "Allocator::namespace",
                "beneficiary": "CCP::namespace",
                "assetId": "A-GILT",
                "requirementId": "REQ-A",
                "quantity": "5.1020408164",
                "effectiveValue": "500.0000000072",
                "location": "Custodian-A",
            },
        )
        residual_payload = dict(offer_payload)
        residual_payload["availableQuantity"] = "4.8979591836"
        residual = contract("offer-new", "CollateralOffer", residual_payload)

        allocations, residuals = validate_accepted_state(
            [allocation, residual],
            [proposal],
            {"A-GILT": original_offer},
            {"REQ-A": requirement},
        )

        self.assertEqual([row.contract_id for row in allocations], ["allocation"])
        self.assertEqual(
            residuals,
            [{"asset_id": "A-GILT", "available_quantity": "4.8979591836"}],
        )

        wrong_residual_payload = dict(residual_payload)
        wrong_residual_payload["availableQuantity"] = "4.0"
        wrong_residual = contract(
            "offer-wrong", "CollateralOffer", wrong_residual_payload
        )
        with self.assertRaisesRegex(RuntimeError, "wrong available quantity"):
            validate_accepted_state(
                [allocation, wrong_residual],
                [proposal],
                {"A-GILT": original_offer},
                {"REQ-A": requirement},
            )


if __name__ == "__main__":
    unittest.main()
