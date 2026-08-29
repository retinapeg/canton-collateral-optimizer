"""Ledger-independent collateral optimisation package."""

from .engine import AllocationResult, optimize_allocation, optimize_collateral

__all__ = ["AllocationResult", "optimize_allocation", "optimize_collateral"]
