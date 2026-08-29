from __future__ import annotations

import argparse
import json
from pathlib import Path

from .engine import optimize_collateral


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find the lowest-cost valid collateral allocation"
    )
    parser.add_argument("market", type=Path, help="Path to a market JSON file")
    args = parser.parse_args()

    with args.market.open(encoding="utf-8") as handle:
        market = json.load(handle)
    print(json.dumps(optimize_collateral(market), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
