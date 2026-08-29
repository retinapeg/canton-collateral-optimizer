"""Lowest-cost collateral allocation using a small linear programme.

The public function in this module deliberately accepts and returns plain
Python dictionaries.  Nothing here knows how Canton stores contracts or how a
backend obtains its authorised ledger view.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import fsum, isfinite
from numbers import Real
from typing import Any

import numpy as np
from scipy.optimize import linprog


_FEASIBILITY_ABSOLUTE_TOLERANCE = 1e-9
_FEASIBILITY_RELATIVE_TOLERANCE = 1e-12


def _required_text(item: Mapping[str, Any], field: str, kind: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{kind}.{field} must be a non-empty string")
    return value


def _required_number(item: Mapping[str, Any], field: str, kind: str) -> float:
    value = item.get(field)
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{kind}.{field} must be a finite number")
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{kind}.{field} must be a finite number")
    return number


def _optional_number_map(
    item: Mapping[str, Any], field: str, kind: str
) -> dict[str, float]:
    raw = item.get(field, {})
    if not isinstance(raw, Mapping):
        raise ValueError(f"{kind}.{field} must be an object")
    values: dict[str, float] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"{kind}.{field} keys must be non-empty strings")
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError(f"{kind}.{field}.{key} must be a finite number")
        number = float(value)
        if not isfinite(number):
            raise ValueError(f"{kind}.{field}.{key} must be a finite number")
        values[key] = number
    return values


def _normalise_market(market: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw_assets = market.get("assets")
    raw_requirements = market.get("requirements")
    if not isinstance(raw_assets, Sequence) or isinstance(raw_assets, (str, bytes)):
        raise ValueError("assets must be a list")
    if not isinstance(raw_requirements, Sequence) or isinstance(
        raw_requirements, (str, bytes)
    ):
        raise ValueError("requirements must be a list")
    if not raw_requirements:
        raise ValueError("requirements must contain at least one requirement")

    assets: list[dict[str, Any]] = []
    asset_ids: set[str] = set()
    for index, raw in enumerate(raw_assets):
        if not isinstance(raw, Mapping):
            raise ValueError(f"assets[{index}] must be an object")
        kind = f"assets[{index}]"
        asset_id = _required_text(raw, "asset_id", kind)
        if asset_id in asset_ids:
            raise ValueError(f"duplicate asset_id: {asset_id}")
        asset_ids.add(asset_id)

        market_value = _required_number(raw, "market_value", kind)
        haircut = _required_number(raw, "haircut", kind)
        opportunity_cost = _required_number(raw, "opportunity_cost", kind)
        available_quantity = _required_number(raw, "available_quantity", kind)
        if market_value <= 0:
            raise ValueError(f"{kind}.market_value must be greater than zero")
        if not 0 <= haircut < 1:
            raise ValueError(f"{kind}.haircut must be in the interval [0, 1)")
        if opportunity_cost < 0:
            raise ValueError(f"{kind}.opportunity_cost cannot be negative")
        if available_quantity < 0:
            raise ValueError(f"{kind}.available_quantity cannot be negative")

        costs_by_requirement = _optional_number_map(
            raw, "costs_by_requirement", kind
        )
        if any(value < 0 for value in costs_by_requirement.values()):
            raise ValueError(
                f"{kind}.costs_by_requirement values cannot be negative"
            )
        haircuts_by_requirement = _optional_number_map(
            raw, "haircuts_by_requirement", kind
        )
        if any(
            value < 0 or value >= 1
            for value in haircuts_by_requirement.values()
        ):
            raise ValueError(
                f"{kind}.haircuts_by_requirement values must be in [0, 1)"
            )
        raw_eligible_requirements = raw.get("eligible_requirements")
        if raw_eligible_requirements is None:
            eligible_requirements = None
        else:
            if not isinstance(raw_eligible_requirements, Sequence) or isinstance(
                raw_eligible_requirements, (str, bytes)
            ):
                raise ValueError(f"{kind}.eligible_requirements must be a list")
            if any(
                not isinstance(value, str) or not value.strip()
                for value in raw_eligible_requirements
            ):
                raise ValueError(
                    f"{kind}.eligible_requirements must contain non-empty strings"
                )
            eligible_requirements = frozenset(raw_eligible_requirements)

        assets.append(
            {
                "asset_id": asset_id,
                "owner": _required_text(raw, "owner", kind),
                "asset_class": _required_text(raw, "asset_class", kind),
                "market_value": market_value,
                "haircut": haircut,
                "opportunity_cost": opportunity_cost,
                "available_quantity": available_quantity,
                "location": str(raw.get("location", "")),
                "costs_by_requirement": costs_by_requirement,
                "haircuts_by_requirement": haircuts_by_requirement,
                "eligible_requirements": eligible_requirements,
            }
        )

    requirements: list[dict[str, Any]] = []
    requirement_ids: set[str] = set()
    for index, raw in enumerate(raw_requirements):
        if not isinstance(raw, Mapping):
            raise ValueError(f"requirements[{index}] must be an object")
        kind = f"requirements[{index}]"
        requirement_id = _required_text(raw, "requirement_id", kind)
        if requirement_id in requirement_ids:
            raise ValueError(f"duplicate requirement_id: {requirement_id}")
        requirement_ids.add(requirement_id)

        required_value = _required_number(raw, "required_effective_value", kind)
        if required_value <= 0:
            raise ValueError(
                f"{kind}.required_effective_value must be greater than zero"
            )
        eligible = raw.get("eligible_asset_classes")
        if not isinstance(eligible, Sequence) or isinstance(eligible, (str, bytes)):
            raise ValueError(f"{kind}.eligible_asset_classes must be a list")
        if not eligible or any(not isinstance(value, str) or not value for value in eligible):
            raise ValueError(
                f"{kind}.eligible_asset_classes must contain non-empty strings"
            )

        requirements.append(
            {
                "requirement_id": requirement_id,
                "obligor": _required_text(raw, "obligor", kind),
                "beneficiary": _required_text(raw, "beneficiary", kind),
                "required_effective_value": required_value,
                "eligible_asset_classes": frozenset(eligible),
            }
        )

    return assets, requirements


def _is_eligible(asset: Mapping[str, Any], requirement: Mapping[str, Any]) -> bool:
    """Return whether an asset/requirement decision variable is permitted.

    An explicit pair eligibility list enables the global institutional model.
    Markets without that list retain the original owner/obligor restriction.
    """

    if asset["asset_class"] not in requirement["eligible_asset_classes"]:
        return False
    eligible_requirements = asset["eligible_requirements"]
    if eligible_requirements is not None:
        return requirement["requirement_id"] in eligible_requirements
    return asset["owner"] == requirement["obligor"]


def _pair_cost(asset: Mapping[str, Any], requirement: Mapping[str, Any]) -> float:
    return asset["costs_by_requirement"].get(
        requirement["requirement_id"], asset["opportunity_cost"]
    )


def _pair_effective_value(
    asset: Mapping[str, Any], requirement: Mapping[str, Any]
) -> float:
    haircut = asset["haircuts_by_requirement"].get(
        requirement["requirement_id"], asset["haircut"]
    )
    return asset["market_value"] * (1.0 - haircut)


def _infeasible(message: str) -> dict[str, Any]:
    return {
        "status": "INFEASIBLE",
        "total_cost": None,
        "allocations": [],
        "requirement_coverage": [],
        "message": message,
    }


def _coverage_is_satisfied(allocated: float, required: float) -> bool:
    tolerance = _FEASIBILITY_ABSOLUTE_TOLERANCE + (
        _FEASIBILITY_RELATIVE_TOLERANCE * max(abs(allocated), abs(required))
    )
    return allocated + tolerance >= required


def _clip_solution_to_availability(
    raw_quantities: Sequence[float],
    pairs: list[tuple[int, int]],
    assets: list[dict[str, Any]],
) -> list[float]:
    """Remove tiny solver bound violations before emitting an exact plan.

    HiGHS uses numerical feasibility tolerances.  Daml's Decimal comparison is
    exact, so an LP value such as 5.0000000005 must never be emitted when the
    available quantity is exactly 5.  Clipping can make a marginal solver result
    under-collateralised; the caller therefore revalidates coverage afterward.
    """

    quantities = [0.0] * len(pairs)
    for asset_index, asset_row in enumerate(assets):
        remaining = asset_row["available_quantity"]
        for pair_index, (pair_asset, _) in enumerate(pairs):
            if pair_asset != asset_index:
                continue
            raw = max(0.0, float(raw_quantities[pair_index]))
            quantity = min(raw, remaining)
            quantities[pair_index] = quantity
            remaining = max(0.0, remaining - quantity)
    return quantities


def optimize_collateral(market: Mapping[str, Any]) -> dict[str, Any]:
    """Return the minimum-cost valid allocation for an authorised market view.

    Each decision variable is a quantity of an eligible asset allocated to one
    requirement.  Availability is constrained across *all* requirements, which
    is the no-double-allocation constraint.
    """

    if not isinstance(market, Mapping):
        raise ValueError("market must be an object")
    assets, requirements = _normalise_market(market)

    pairs = [
        (asset_index, requirement_index)
        for asset_index, asset_row in enumerate(assets)
        for requirement_index, requirement_row in enumerate(requirements)
        if _is_eligible(asset_row, requirement_row)
    ]
    if not pairs:
        return _infeasible("No asset is eligible for any requirement")

    for requirement_index, requirement_row in enumerate(requirements):
        if not any(pair[1] == requirement_index for pair in pairs):
            return _infeasible(
                f"No eligible collateral for {requirement_row['requirement_id']}"
            )

    objective = np.array(
        [
            _pair_cost(assets[asset_index], requirements[requirement_index])
            for asset_index, requirement_index in pairs
        ],
        dtype=float,
    )

    constraint_rows: list[list[float]] = []
    constraint_bounds: list[float] = []

    # Sum_j x_ij <= q_i: one row per asset prevents double allocation.
    for asset_index, asset_row in enumerate(assets):
        constraint_rows.append(
            [1.0 if pair_asset == asset_index else 0.0 for pair_asset, _ in pairs]
        )
        constraint_bounds.append(asset_row["available_quantity"])

    # linprog accepts <= rows, so multiply collateral coverage constraints by -1.
    for requirement_index, requirement_row in enumerate(requirements):
        constraint_rows.append(
            [
                -_pair_effective_value(
                    assets[pair_asset], requirements[pair_requirement]
                )
                if pair_requirement == requirement_index
                else 0.0
                for pair_asset, pair_requirement in pairs
            ]
        )
        constraint_bounds.append(-requirement_row["required_effective_value"])

    solution = linprog(
        c=objective,
        A_ub=np.array(constraint_rows, dtype=float),
        b_ub=np.array(constraint_bounds, dtype=float),
        bounds=[(0.0, None)] * len(pairs),
        method="highs",
        options={
            "primal_feasibility_tolerance": 1e-10,
            "dual_feasibility_tolerance": 1e-10,
        },
    )

    if solution.status == 2:
        return _infeasible("Available eligible collateral cannot satisfy all requirements")
    if not solution.success:
        return {
            "status": "ERROR",
            "total_cost": None,
            "allocations": [],
            "requirement_coverage": [],
            "message": solution.message,
        }

    emitted_quantities = _clip_solution_to_availability(
        solution.x, pairs, assets
    )
    allocations: list[dict[str, Any]] = []
    contributions_by_requirement: list[list[float]] = [
        [] for _ in requirements
    ]
    emitted_costs: list[float] = []
    for quantity, (asset_index, requirement_index) in zip(
        emitted_quantities, pairs
    ):
        if quantity <= 0.0:
            continue
        asset_row = assets[asset_index]
        requirement_row = requirements[requirement_index]
        effective_value = quantity * _pair_effective_value(
            asset_row, requirement_row
        )
        cost = quantity * _pair_cost(asset_row, requirement_row)
        contributions_by_requirement[requirement_index].append(effective_value)
        emitted_costs.append(cost)
        allocations.append(
            {
                "asset_id": asset_row["asset_id"],
                "owner": asset_row["owner"],
                "requirement_id": requirement_row["requirement_id"],
                "quantity": float(quantity),
                "effective_value": float(effective_value),
                "cost": float(cost),
            }
        )

    coverage = []
    for index, requirement_row in enumerate(requirements):
        allocated = fsum(contributions_by_requirement[index])
        required = requirement_row["required_effective_value"]
        satisfied = _coverage_is_satisfied(allocated, required)
        coverage.append(
            {
                "requirement_id": requirement_row["requirement_id"],
                "required_effective_value": required,
                "allocated_effective_value": float(allocated),
                "satisfied": satisfied,
            }
        )

    if not all(row["satisfied"] for row in coverage):
        return _infeasible(
            "Solver output is not feasible after enforcing exact availability bounds"
        )

    # This is both an invariant check and documentation of the quantity shared
    # across requirements.  The clipping pass should make any violation
    # impossible, even when the numerical solver returned a tolerance-level one.
    for asset_index, asset_row in enumerate(assets):
        emitted_total = fsum(
            quantity
            for quantity, (pair_asset, _) in zip(emitted_quantities, pairs)
            if pair_asset == asset_index
        )
        if emitted_total > asset_row["available_quantity"]:
            return {
                "status": "ERROR",
                "total_cost": None,
                "allocations": [],
                "requirement_coverage": [],
                "message": f"Post-solve availability validation failed for {asset_row['asset_id']}",
            }

    return {
        "status": "OPTIMAL",
        "total_cost": float(fsum(emitted_costs)),
        "allocations": allocations,
        "requirement_coverage": coverage,
        "message": "Optimal collateral allocation found",
    }
