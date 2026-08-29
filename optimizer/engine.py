"""Lowest-cost collateral allocation using a small linear programme.

The public function in this module deliberately accepts and returns plain
Python dictionaries.  Nothing here knows how Canton stores contracts or how a
backend obtains its authorised ledger view.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import fsum, isfinite
from numbers import Real
from typing import Any

import numpy as np
from scipy.optimize import linprog


_FEASIBILITY_ABSOLUTE_TOLERANCE = 1e-9
_FEASIBILITY_RELATIVE_TOLERANCE = 1e-12


@dataclass(frozen=True)
class AllocationResult:
    """Plain-Python result from the ledger-independent allocation LP."""

    success: bool
    total_cost: float | None
    allocations: dict[str, dict[str, float]]
    status: str
    message: str


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
                "effective_value_per_unit": market_value * (1.0 - haircut),
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
    """Apply the two legal eligibility rules in the demo model.

    The asset class must be accepted by the requirement, and the collateral
    owner must be the obligation's obligor.  The second rule prevents one bank's
    inventory from silently covering another bank's obligation.
    """

    return (
        asset["asset_class"] in requirement["eligible_asset_classes"]
        and asset["owner"] == requirement["obligor"]
    )


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


def _normalise_amounts(
    values: Mapping[str, Real], name: str, *, require_values: bool
) -> dict[str, float]:
    if not isinstance(values, Mapping):
        raise ValueError(f"{name} must be an object")
    if require_values and not values:
        raise ValueError(f"{name} must contain at least one entry")

    normalised: dict[str, float] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"{name} keys must be non-empty strings")
        if isinstance(value, bool) or not isinstance(value, Real):
            raise ValueError(f"{name}[{key!r}] must be a finite number")
        number = float(value)
        if not isfinite(number):
            raise ValueError(f"{name}[{key!r}] must be a finite number")
        if number < 0:
            raise ValueError(f"{name}[{key!r}] cannot be negative")
        normalised[key] = number
    return normalised


def _normalise_numeric_matrix(
    matrix: Mapping[str, Mapping[str, Real]],
    name: str,
    row_ids: Sequence[str],
    column_ids: Sequence[str],
    *,
    haircut: bool = False,
) -> dict[str, dict[str, float]]:
    if not isinstance(matrix, Mapping):
        raise ValueError(f"{name} must be an object")

    normalised: dict[str, dict[str, float]] = {}
    for row_id in row_ids:
        row = matrix.get(row_id)
        if not isinstance(row, Mapping):
            raise ValueError(f"{name}[{row_id!r}] must be an object")
        normalised[row_id] = {}
        for column_id in column_ids:
            value = row.get(column_id)
            if isinstance(value, bool) or not isinstance(value, Real):
                raise ValueError(
                    f"{name}[{row_id!r}][{column_id!r}] must be a finite number"
                )
            number = float(value)
            if not isfinite(number):
                raise ValueError(
                    f"{name}[{row_id!r}][{column_id!r}] must be a finite number"
                )
            if haircut and not 0.0 <= number <= 1.0:
                raise ValueError(
                    f"{name}[{row_id!r}][{column_id!r}] must be in [0, 1]"
                )
            normalised[row_id][column_id] = number
    return normalised


def _normalise_eligibility(
    matrix: Mapping[str, Mapping[str, bool]],
    asset_ids: Sequence[str],
    destination_ids: Sequence[str],
) -> dict[str, dict[str, bool]]:
    if not isinstance(matrix, Mapping):
        raise ValueError("eligibility must be an object")

    normalised: dict[str, dict[str, bool]] = {}
    for asset_id in asset_ids:
        row = matrix.get(asset_id)
        if not isinstance(row, Mapping):
            raise ValueError(f"eligibility[{asset_id!r}] must be an object")
        normalised[asset_id] = {}
        for destination_id in destination_ids:
            value = row.get(destination_id)
            if not isinstance(value, bool):
                raise ValueError(
                    f"eligibility[{asset_id!r}][{destination_id!r}] must be a boolean"
                )
            normalised[asset_id][destination_id] = value
    return normalised


def _empty_allocation_matrix(
    asset_ids: Sequence[str], destination_ids: Sequence[str]
) -> dict[str, dict[str, float]]:
    return {
        asset_id: {destination_id: 0.0 for destination_id in destination_ids}
        for asset_id in asset_ids
    }


def optimize_allocation(
    *,
    supplies: Mapping[str, Real],
    demands: Mapping[str, Real],
    costs: Mapping[str, Mapping[str, Real]],
    haircuts: Mapping[str, Mapping[str, Real]],
    eligibility: Mapping[str, Mapping[str, bool]],
) -> AllocationResult:
    """Minimise global collateral cost with one linear programme.

    ``allocations[asset][destination]`` is x[i,j].  Ineligible pairs are kept
    at zero through fixed LP bounds.  This function has no wallet, Daml, Canton,
    mandate, or authorisation responsibilities.
    """

    normalised_supplies = _normalise_amounts(
        supplies, "supplies", require_values=False
    )
    normalised_demands = _normalise_amounts(
        demands, "demands", require_values=True
    )
    asset_ids = list(normalised_supplies)
    destination_ids = list(normalised_demands)
    normalised_costs = _normalise_numeric_matrix(
        costs, "costs", asset_ids, destination_ids
    )
    normalised_haircuts = _normalise_numeric_matrix(
        haircuts,
        "haircuts",
        asset_ids,
        destination_ids,
        haircut=True,
    )
    normalised_eligibility = _normalise_eligibility(
        eligibility, asset_ids, destination_ids
    )
    empty_allocations = _empty_allocation_matrix(asset_ids, destination_ids)

    if not asset_ids:
        return AllocationResult(
            success=False,
            total_cost=None,
            allocations=empty_allocations,
            status="INFEASIBLE",
            message="No collateral assets are available",
        )

    pairs = [
        (asset_id, destination_id)
        for asset_id in asset_ids
        for destination_id in destination_ids
    ]
    objective = np.array(
        [normalised_costs[asset_id][destination_id] for asset_id, destination_id in pairs],
        dtype=float,
    )

    constraint_rows: list[list[float]] = []
    constraint_bounds: list[float] = []

    # Sum_j x[i,j] <= supply[i].
    for asset_id in asset_ids:
        constraint_rows.append(
            [1.0 if pair_asset == asset_id else 0.0 for pair_asset, _ in pairs]
        )
        constraint_bounds.append(normalised_supplies[asset_id])

    # linprog accepts <= rows, so negate each required-coverage constraint.
    for destination_id in destination_ids:
        constraint_rows.append(
            [
                -(1.0 - normalised_haircuts[pair_asset][pair_destination])
                if pair_destination == destination_id
                else 0.0
                for pair_asset, pair_destination in pairs
            ]
        )
        constraint_bounds.append(-normalised_demands[destination_id])

    bounds = [
        (0.0, None)
        if normalised_eligibility[asset_id][destination_id]
        else (0.0, 0.0)
        for asset_id, destination_id in pairs
    ]
    solution = linprog(
        c=objective,
        A_ub=np.array(constraint_rows, dtype=float),
        b_ub=np.array(constraint_bounds, dtype=float),
        bounds=bounds,
        method="highs",
        options={
            "primal_feasibility_tolerance": 1e-10,
            "dual_feasibility_tolerance": 1e-10,
        },
    )

    if solution.status == 2:
        return AllocationResult(
            success=False,
            total_cost=None,
            allocations=empty_allocations,
            status="INFEASIBLE",
            message="Available eligible collateral cannot satisfy all demands",
        )
    if not solution.success:
        return AllocationResult(
            success=False,
            total_cost=None,
            allocations=empty_allocations,
            status="ERROR",
            message=solution.message,
        )

    allocations = _empty_allocation_matrix(asset_ids, destination_ids)
    for asset_id in asset_ids:
        remaining = normalised_supplies[asset_id]
        for pair_index, (pair_asset, destination_id) in enumerate(pairs):
            if pair_asset != asset_id:
                continue
            if not normalised_eligibility[asset_id][destination_id]:
                quantity = 0.0
            else:
                raw_quantity = max(0.0, float(solution.x[pair_index]))
                quantity = min(raw_quantity, remaining)
            allocations[asset_id][destination_id] = quantity
            remaining = max(0.0, remaining - quantity)

    for destination_id in destination_ids:
        allocated_effective_value = fsum(
            (1.0 - normalised_haircuts[asset_id][destination_id])
            * allocations[asset_id][destination_id]
            for asset_id in asset_ids
        )
        if not _coverage_is_satisfied(
            allocated_effective_value, normalised_demands[destination_id]
        ):
            return AllocationResult(
                success=False,
                total_cost=None,
                allocations=empty_allocations,
                status="INFEASIBLE",
                message=(
                    "Solver output is not feasible after enforcing exact supply bounds"
                ),
            )

    total_cost = fsum(
        normalised_costs[asset_id][destination_id]
        * allocations[asset_id][destination_id]
        for asset_id, destination_id in pairs
    )
    return AllocationResult(
        success=True,
        total_cost=float(total_cost),
        allocations=allocations,
        status="OPTIMAL",
        message="Optimal collateral allocation found",
    )


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

    asset_ids = [asset_row["asset_id"] for asset_row in assets]
    requirement_ids = [
        requirement_row["requirement_id"] for requirement_row in requirements
    ]
    supplies = {
        asset_row["asset_id"]: (
            asset_row["available_quantity"] * asset_row["market_value"]
        )
        for asset_row in assets
    }
    demands = {
        requirement_row["requirement_id"]: requirement_row[
            "required_effective_value"
        ]
        for requirement_row in requirements
    }
    costs = {
        asset_row["asset_id"]: {
            requirement_id: (
                asset_row["opportunity_cost"] / asset_row["market_value"]
            )
            for requirement_id in requirement_ids
        }
        for asset_row in assets
    }
    haircuts = {
        asset_row["asset_id"]: {
            requirement_id: asset_row["haircut"]
            for requirement_id in requirement_ids
        }
        for asset_row in assets
    }
    eligibility = {
        asset_row["asset_id"]: {
            requirement_row["requirement_id"]: _is_eligible(
                asset_row, requirement_row
            )
            for requirement_row in requirements
        }
        for asset_row in assets
    }

    allocation_result = optimize_allocation(
        supplies=supplies,
        demands=demands,
        costs=costs,
        haircuts=haircuts,
        eligibility=eligibility,
    )
    if allocation_result.status == "INFEASIBLE":
        return _infeasible(allocation_result.message)
    if not allocation_result.success:
        return {
            "status": "ERROR",
            "total_cost": None,
            "allocations": [],
            "requirement_coverage": [],
            "message": allocation_result.message,
        }

    raw_quantities = [
        allocation_result.allocations[asset_ids[asset_index]][
            requirement_ids[requirement_index]
        ]
        / assets[asset_index]["market_value"]
        for asset_index, requirement_index in pairs
    ]
    emitted_quantities = _clip_solution_to_availability(
        raw_quantities, pairs, assets
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
        effective_value = quantity * asset_row["effective_value_per_unit"]
        cost = quantity * asset_row["opportunity_cost"]
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
