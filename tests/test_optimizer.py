from __future__ import annotations

import unittest
import json
from pathlib import Path

from optimizer.engine import optimize_collateral


def asset(
    asset_id: str,
    *,
    asset_class: str = "GOVERNMENT_BOND",
    market_value: float = 100.0,
    haircut: float = 0.0,
    opportunity_cost: float = 1.0,
    available_quantity: float = 10.0,
    owner: str = "BankA",
) -> dict[str, object]:
    return {
        "asset_id": asset_id,
        "owner": owner,
        "asset_class": asset_class,
        "market_value": market_value,
        "haircut": haircut,
        "opportunity_cost": opportunity_cost,
        "available_quantity": available_quantity,
        "location": "TEST",
    }


def requirement(
    requirement_id: str,
    required_effective_value: float,
    *,
    eligible_asset_classes: list[str] | None = None,
    obligor: str = "BankA",
) -> dict[str, object]:
    return {
        "requirement_id": requirement_id,
        "obligor": obligor,
        "beneficiary": "CCP",
        "required_effective_value": required_effective_value,
        "eligible_asset_classes": eligible_asset_classes
        or ["GOVERNMENT_BOND"],
    }


class OptimizerTests(unittest.TestCase):
    def test_global_allocation_beats_greedy_local_choice(self) -> None:
        asset_1 = asset(
            "Asset1",
            market_value=1.0,
            available_quantity=1.0,
            owner="InstitutionA",
        )
        asset_1.update(
            {
                "costs_by_requirement": {
                    "InstitutionB": 1.0,
                    "InstitutionC": 2.0,
                },
                "haircuts_by_requirement": {
                    "InstitutionB": 0.0,
                    "InstitutionC": 0.0,
                },
                "eligible_requirements": ["InstitutionB", "InstitutionC"],
            }
        )
        asset_2 = asset(
            "Asset2",
            market_value=1.0,
            available_quantity=1.0,
            owner="InstitutionA",
        )
        asset_2.update(
            {
                "costs_by_requirement": {
                    "InstitutionB": 1.1,
                    "InstitutionC": 100.0,
                },
                "haircuts_by_requirement": {
                    "InstitutionB": 0.0,
                    "InstitutionC": 0.0,
                },
                "eligible_requirements": ["InstitutionB", "InstitutionC"],
            }
        )
        market = {
            "assets": [asset_1, asset_2],
            "requirements": [
                requirement("InstitutionB", 1.0, obligor="InstitutionB"),
                requirement("InstitutionC", 1.0, obligor="InstitutionC"),
            ],
        }

        result = optimize_collateral(market)

        self.assertEqual(result["status"], "OPTIMAL")
        self.assertAlmostEqual(result["total_cost"], 3.1, places=7)
        allocation = {
            (row["asset_id"], row["requirement_id"]): row["quantity"]
            for row in result["allocations"]
        }
        self.assertAlmostEqual(allocation[("Asset2", "InstitutionB")], 1.0)
        self.assertAlmostEqual(allocation[("Asset1", "InstitutionC")], 1.0)
        self.assertNotIn(("Asset1", "InstitutionB"), allocation)
        self.assertNotIn(("Asset2", "InstitutionC"), allocation)

    def test_sample_market_has_the_expected_deterministic_solution(self) -> None:
        sample_path = Path(__file__).parents[1] / "sample_data" / "market.json"
        with sample_path.open(encoding="utf-8") as handle:
            market = json.load(handle)

        result = optimize_collateral(market)

        # The public interface promises ordinary JSON-compatible values.
        json.dumps(result)

        self.assertEqual(result["status"], "OPTIMAL")
        allocation_by_requirement = {
            row["requirement_id"]: row for row in result["allocations"]
        }
        self.assertEqual(
            allocation_by_requirement["REQ-BANK-A-CCP"]["asset_id"],
            "A-GILT-2030",
        )
        self.assertAlmostEqual(
            allocation_by_requirement["REQ-BANK-A-CCP"]["quantity"],
            500.0 / 98.0,
            places=7,
        )
        self.assertEqual(
            allocation_by_requirement["REQ-BANK-B-CCP"]["asset_id"],
            "B-CORP-2029",
        )
        self.assertAlmostEqual(
            allocation_by_requirement["REQ-BANK-B-CCP"]["quantity"],
            5.0,
            places=7,
        )

    def test_known_case_uses_the_cheapest_solution(self) -> None:
        market = {
            "assets": [
                asset("CHEAP", opportunity_cost=1.0),
                asset("EXPENSIVE", opportunity_cost=3.0),
            ],
            "requirements": [requirement("REQ-1", 500.0)],
        }

        result = optimize_collateral(market)

        self.assertEqual(result["status"], "OPTIMAL")
        self.assertAlmostEqual(result["total_cost"], 5.0, places=7)
        self.assertEqual(len(result["allocations"]), 1)
        self.assertEqual(result["allocations"][0]["asset_id"], "CHEAP")
        self.assertAlmostEqual(result["allocations"][0]["quantity"], 5.0)

    def test_no_asset_is_allocated_more_than_available(self) -> None:
        market = {
            "assets": [
                asset("LIMITED", opportunity_cost=1.0, available_quantity=3.0),
                asset("FALLBACK", opportunity_cost=2.0, available_quantity=10.0),
            ],
            "requirements": [requirement("REQ-1", 500.0)],
        }

        result = optimize_collateral(market)

        allocated = {
            row["asset_id"]: row["quantity"] for row in result["allocations"]
        }
        self.assertLessEqual(allocated["LIMITED"], 3.0 + 1e-8)

    def test_every_requirement_is_satisfied(self) -> None:
        market = {
            "assets": [asset("POOL", available_quantity=10.0)],
            "requirements": [
                requirement("REQ-1", 300.0),
                requirement("REQ-2", 400.0),
            ],
        }

        result = optimize_collateral(market)

        self.assertEqual(result["status"], "OPTIMAL")
        for coverage in result["requirement_coverage"]:
            self.assertGreaterEqual(
                coverage["allocated_effective_value"] + 1e-7,
                coverage["required_effective_value"],
            )
            self.assertTrue(coverage["satisfied"])

    def test_ineligible_assets_are_never_used(self) -> None:
        market = {
            "assets": [
                asset(
                    "INELIGIBLE_CHEAP",
                    asset_class="CORPORATE_BOND",
                    opportunity_cost=0.01,
                ),
                asset("ELIGIBLE", opportunity_cost=2.0),
            ],
            "requirements": [
                requirement(
                    "REQ-1",
                    500.0,
                    eligible_asset_classes=["GOVERNMENT_BOND"],
                )
            ],
        }

        result = optimize_collateral(market)

        used_assets = {row["asset_id"] for row in result["allocations"]}
        self.assertNotIn("INELIGIBLE_CHEAP", used_assets)
        self.assertEqual(used_assets, {"ELIGIBLE"})

    def test_insufficient_collateral_returns_infeasible_cleanly(self) -> None:
        market = {
            "assets": [asset("TOO_SMALL", available_quantity=1.0)],
            "requirements": [requirement("REQ-1", 500.0)],
        }

        result = optimize_collateral(market)

        self.assertEqual(result["status"], "INFEASIBLE")
        self.assertIsNone(result["total_cost"])
        self.assertEqual(result["allocations"], [])

    def test_no_double_allocation_across_requirements(self) -> None:
        market = {
            "assets": [
                asset("SHARED", opportunity_cost=1.0, available_quantity=6.0),
                asset("FALLBACK", opportunity_cost=5.0, available_quantity=10.0),
            ],
            "requirements": [
                requirement("REQ-1", 400.0),
                requirement("REQ-2", 400.0),
            ],
        }

        result = optimize_collateral(market)

        shared_total = sum(
            row["quantity"]
            for row in result["allocations"]
            if row["asset_id"] == "SHARED"
        )
        self.assertLessEqual(shared_total, 6.0 + 1e-8)
        self.assertAlmostEqual(shared_total, 6.0, places=7)

    def test_solver_tolerance_cannot_exceed_exact_availability(self) -> None:
        market = {
            "assets": [asset("BOUNDARY", available_quantity=5.0)],
            "requirements": [requirement("REQ-1", 500.00000005)],
        }

        result = optimize_collateral(market)

        self.assertEqual(result["status"], "INFEASIBLE")
        self.assertEqual(result["allocations"], [])

    def test_tiny_but_required_allocation_is_not_dropped(self) -> None:
        market = {
            "assets": [
                asset(
                    "HIGH_VALUE",
                    market_value=1_000_000_000_000.0,
                    available_quantity=1.0,
                )
            ],
            "requirements": [requirement("REQ-1", 100.0)],
        }

        result = optimize_collateral(market)

        self.assertEqual(result["status"], "OPTIMAL")
        self.assertEqual(len(result["allocations"]), 1)
        self.assertGreater(result["allocations"][0]["quantity"], 0.0)
        self.assertTrue(result["requirement_coverage"][0]["satisfied"])

    def test_collateral_cannot_cover_another_banks_obligation(self) -> None:
        market = {
            "assets": [
                asset("BANK_A_CHEAP", owner="BankA", opportunity_cost=0.01),
                asset("BANK_B", owner="BankB", opportunity_cost=2.0),
            ],
            "requirements": [
                requirement("BANK_B_REQ", 500.0, obligor="BankB")
            ],
        }

        result = optimize_collateral(market)

        used_assets = {row["asset_id"] for row in result["allocations"]}
        self.assertEqual(used_assets, {"BANK_B"})


if __name__ == "__main__":
    unittest.main()
