from __future__ import annotations

import argparse
import json
from math import isclose
from pathlib import Path

from .engine import optimize_allocation, optimize_collateral


def run_global_demo() -> None:
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
    local_cost = costs["Asset1"]["InstitutionB"] + costs["Asset2"]["InstitutionC"]
    result = optimize_allocation(
        supplies=supplies,
        demands=demands,
        costs=costs,
        haircuts=haircuts,
        eligibility=eligibility,
    )

    print("GLOBAL COLLATERAL OPTIMIZER")
    print(f"\nGreedy/local example cost: {local_cost:.1f}")
    print("\nGlobal allocation:")
    for destination_id in demands:
        for asset_id in supplies:
            quantity = result.allocations[asset_id][destination_id]
            if quantity > 0.0:
                print(f"{asset_id} -> {destination_id}: {quantity:.1f}")

    if result.total_cost is None:
        print(f"\nOptimizer failed: {result.status} - {result.message}")
        print("\nOPTIMIZER FAIL")
        return

    print(f"\nGlobal optimum cost: {result.total_cost:.1f}")
    print(f"Savings vs local allocation: {local_cost - result.total_cost:.1f}")
    passed = (
        result.success
        and isclose(result.total_cost, 3.1, abs_tol=1e-9)
        and isclose(
            result.allocations["Asset2"]["InstitutionB"], 1.0, abs_tol=1e-9
        )
        and isclose(
            result.allocations["Asset1"]["InstitutionC"], 1.0, abs_tol=1e-9
        )
    )
    print("\nOPTIMIZER PASS" if passed else "\nOPTIMIZER FAIL")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find the lowest-cost valid collateral allocation"
    )
    parser.add_argument(
        "market",
        type=Path,
        nargs="?",
        help="Path to a market JSON file; omit it to run the global-allocation demo",
    )
    args = parser.parse_args()

    if args.market is None:
        run_global_demo()
        return

    with args.market.open(encoding="utf-8") as handle:
        market = json.load(handle)
    print(json.dumps(optimize_collateral(market), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
