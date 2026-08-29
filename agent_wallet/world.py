"""A synthetic company for the agent to run, with plausible things to buy.

None of this is real, and none of it is scraped from anywhere: the vendors are
invented, and the amounts are drawn from ranges chosen to look like a small
studio's monthly infrastructure bill.  The point is to give the agent a stream
of decisions that resemble a real workload, so the wallet is exercised the way
it would be in production rather than by two hand-written purchases.

What makes it a useful test rather than a toy is the shape of the traffic:
mostly small and frequent, occasionally large, with the expensive things
clustered (a training run costs money three times in a row, not once).  That is
the pattern that finds cap bugs.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import random


@dataclass(frozen=True)
class Vendor:
    hint: str            # the Daml party hint
    label: str           # what a human calls it
    category: str
    approved: bool       # is it on the mandate's allow-list
    low: str             # typical spend, low end
    high: str
    reasons: tuple[str, ...]

    def amount(self, rng: random.Random) -> Decimal:
        low, high = Decimal(self.low), Decimal(self.high)
        pennies = rng.randint(int(low * 100), int(high * 100))
        return (Decimal(pennies) / 100).quantize(Decimal("0.01"))

    def reason(self, rng: random.Random) -> str:
        return rng.choice(self.reasons)


# The studio's approved suppliers.  An ops agent pays these all day.
APPROVED = (
    Vendor(
        hint="NimbusCloud",
        label="Nimbus Cloud",
        category="compute",
        approved=True,
        low="12.00",
        high="94.00",
        reasons=(
            "burst capacity for the nightly render",
            "autoscaling group scaled out",
            "spot instances for batch job 4471",
            "egress overage on the asset bucket",
        ),
    ),
    Vendor(
        hint="TokenMill",
        label="TokenMill Inference",
        category="model inference",
        approved=True,
        low="0.80",
        high="46.00",
        reasons=(
            "summarising the support backlog",
            "embedding 12k new documents",
            "batch classification run",
            "re-ranking search results",
            "transcribing customer calls",
        ),
    ),
    Vendor(
        hint="VectorStore",
        label="VectorStore",
        category="storage",
        approved=True,
        low="3.50",
        high="28.00",
        reasons=(
            "index rebuild after schema change",
            "monthly storage, 240 GB",
            "replica in a second region",
        ),
    ),
    Vendor(
        hint="PagerWatch",
        label="PagerWatch",
        category="monitoring",
        approved=True,
        low="29.00",
        high="29.00",          # a flat subscription, so it never varies
        reasons=("monthly monitoring subscription",),
    ),
    Vendor(
        hint="DeskSupply",
        label="Desk Supply Co",
        category="office",
        approved=True,
        low="6.00",
        high="52.00",
        reasons=(
            "printer toner",
            "replacement keyboard",
            "coffee for the studio",
        ),
    ),
)

# Nobody approved these.  The mandate's allow-list is the only thing stopping
# the agent paying them, and it is the thing a phishing invoice attacks.
UNAPPROVED = (
    Vendor(
        hint="InvoiceBot",
        label="Accounts Receivable",
        category="phishing",
        approved=False,
        low="240.00",
        high="1200.00",
        reasons=(
            "FINAL NOTICE: overdue cloud invoice, pay within 24h",
            "urgent: your account will be suspended, settle now",
            "updated bank details for your usual supplier",
        ),
    ),
    Vendor(
        hint="GreyMarket",
        label="Grey Market GPUs",
        category="marketplace",
        approved=False,
        low="800.00",
        high="3400.00",
        reasons=(
            "discounted H100 hours, cash only",
            "bulk credits, no invoice",
        ),
    ),
)

ALL_VENDORS = APPROVED + UNAPPROVED
VENDOR_HINTS = tuple(v.hint for v in ALL_VENDORS)
APPROVED_HINTS = tuple(v.hint for v in APPROVED)

# The studio's own parties.
BANK = "Treasury"
OWNER = "Studio"
AGENT = "OpsAgent"
PARTY_HINTS = (BANK, OWNER, AGENT) + VENDOR_HINTS


@dataclass(frozen=True)
class Job:
    """One thing the agent has decided to pay for."""

    vendor: Vendor
    amount: Decimal
    reason: str
    kind: str = "routine"     # routine | incident


def routine_workload(rng: random.Random, count: int) -> list[Job]:
    """A day's ordinary spending.

    Weighted so inference and compute dominate, the way they would if an agent
    were actually running something.
    """
    weights = {
        "TokenMill": 8,
        "NimbusCloud": 5,
        "VectorStore": 3,
        "DeskSupply": 2,
        "PagerWatch": 1,
    }
    pool: list[Vendor] = []
    for vendor in APPROVED:
        pool.extend([vendor] * weights[vendor.hint])
    jobs = []
    for _ in range(count):
        vendor = rng.choice(pool)
        jobs.append(
            Job(vendor=vendor, amount=vendor.amount(rng), reason=vendor.reason(rng))
        )
    return jobs


def vendor_by_hint(hint: str) -> Vendor:
    for vendor in ALL_VENDORS:
        if vendor.hint == hint:
            return vendor
    raise KeyError(hint)
