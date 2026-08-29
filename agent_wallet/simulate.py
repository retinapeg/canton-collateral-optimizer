"""Run the agent live against a synthetic company, and watch the wallet hold.

    python -m agent_wallet.simulate                 # ~3 minutes, watchable
    python -m agent_wallet.simulate --speed 8       # fast, for a recording
    python -m agent_wallet.simulate --seed 7        # reproduce a run exactly

Open http://localhost:7575 beside it.

Unlike `demo.py`, which walks a fixed script, this generates a plausible
workload (see `world.py`) and lets the agent spend into it continuously.  The
limits are not hit because a script decided to hit them -- they are hit because
the agent kept working until it ran into them, which is how they would be hit
in production.

Three incidents are injected on the way, because a real agent meets all three:

  * a phishing invoice from a vendor nobody approved
  * a runaway retry loop, where the agent keeps buying the same expensive thing
  * the owner deciding, mid-flight, that this has gone far enough

The per-period window is deliberately short (60s by default, not a day) so you
can watch it fill up, block spending, and then reset while the run continues.
That is the one limit `daml test` can only demonstrate with `passTime`.

Exits non-zero if the agent ever got past a limit.
"""

from __future__ import annotations

import argparse
from datetime import timedelta
from decimal import Decimal
import random
import sys
import time

from backend.canton import LedgerApiError

from . import network
from . import world
from .ledger import Mandate, Refused, Wallet, money, party_hint

TREASURY = Decimal("50000")
CAP = Decimal("2500")
PERIOD_LIMIT = Decimal("250")

GREEN = "\033[32m"
RED = "\033[31m"
DIM = "\033[2m"
BOLD = "\033[1m"
OFF = "\033[0m"


class Simulation:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.rng = random.Random(args.seed)
        self.client, self.target = network.client_from_env(args.base_url)
        self.wallet = Wallet(self.client)
        self.colour = sys.stdout.isatty() and not args.no_colour

        self.paid = 0
        self.refused = 0
        self.refused_by: dict[str, int] = {}
        self.spent = Decimal("0")
        self.breaches: list[str] = []
        self.reference = f"ops-{int(time.time())}"
        self._last_was_period_refusal = False

    # -- output --------------------------------------------------------------

    def c(self, code: str, text: str) -> str:
        return f"{code}{text}{OFF}" if self.colour else text

    def event(self, ok: bool, vendor: str, amount: Decimal, note: str, tail: str) -> None:
        mark = self.c(GREEN, "  PAID  ") if ok else self.c(RED, "REFUSED ")
        stamp = self.c(DIM, time.strftime("%H:%M:%S"))
        print(
            f"{stamp} {mark} {vendor:<20} {money(amount):>9}  "
            f"{note[:36]:<36} {self.c(DIM, tail)}"
        )

    def banner(self, text: str) -> None:
        print(f"\n{self.c(BOLD, text)}\n{'-' * 96}")

    def pause(self, seconds: float) -> None:
        time.sleep(seconds / max(self.args.speed, 0.01))

    # -- setup ---------------------------------------------------------------

    def setup(self) -> tuple[Mandate, str, dict[str, str]]:
        self.client.ledger_end()
        parties = self.wallet.ensure_parties(world.PARTY_HINTS)
        owner, agent = parties[world.OWNER], parties[world.AGENT]

        account = self.wallet.open_account(
            bank=parties[world.BANK],
            owner=owner,
            balance=TREASURY,
            viewers=[agent],
        )
        authority = self.wallet.issue_authority(
            owner=owner, agent=agent, label="ops-agent-key"
        )
        proposal = self.wallet.propose_mandate(
            owner=owner,
            agent=agent,
            account_cid=account,
            authority_cid=authority,
            reference=self.reference,
            cap=CAP,
            period_limit=PERIOD_LIMIT,
            period_length=timedelta(seconds=self.args.period_seconds),
            allowed_payees=[parties[h] for h in world.APPROVED_HINTS],
            expires_in=timedelta(days=1),
        )
        mandate = self.wallet.accept_mandate(agent=agent, proposal_cid=proposal)

        self.banner("THE STUDIO HIRES AN OPS AGENT")
        print(f"  Treasury            {money(TREASURY)}")
        print(f"  Agent's total cap   {money(CAP)}")
        print(
            f"  Per-period limit    {money(PERIOD_LIMIT)} "
            f"every {self.args.period_seconds}s"
        )
        print(
            "  Approved vendors    "
            + ", ".join(v.label for v in world.APPROVED)
        )
        print(
            "  Not approved        "
            + ", ".join(v.label for v in world.UNAPPROVED)
        )
        print(f"\n  The agent can see all {money(TREASURY)} and may spend {money(CAP)}.")
        print(f"  Watch it at http://localhost:{self.args.watch_port}\n")
        return mandate, authority, parties

    # -- spending ------------------------------------------------------------

    def attempt(
        self, mandate: Mandate, agent: str, party: str, job: world.Job
    ) -> Mandate:
        """Try to pay. The ledger decides; we only report."""
        try:
            updated = self.wallet.charge(
                agent=agent,
                mandate_cid=mandate.contract_id,
                payee=party,
                amount=job.amount,
                memo=job.reason,
            )
        except Refused as exc:
            self.refused += 1
            reason = exc.reason
            key = _classify(reason)
            self.refused_by[key] = self.refused_by.get(key, 0) + 1
            self._last_was_period_refusal = (key == "would exceed the period limit")
            self.event(False, job.vendor.label, job.amount, job.reason, reason[:44])
            if job.kind == "incident" and key == "unknown":
                self.breaches.append(f"unexpected refusal: {reason}")
            return mandate
        except LedgerApiError as exc:
            self.event(False, job.vendor.label, job.amount, job.reason, str(exc)[:44])
            return mandate

        self.paid += 1
        self.spent += job.amount
        if not job.vendor.approved:
            self.breaches.append(
                f"paid unapproved vendor {job.vendor.label} {money(job.amount)}"
            )
        if updated.spent > updated.cap:
            self.breaches.append(f"spent {updated.spent} over cap {updated.cap}")
        self.event(
            True,
            job.vendor.label,
            job.amount,
            job.reason,
            f"left {money(updated.remaining)}",
        )
        return updated

    # -- the run -------------------------------------------------------------

    def run(self) -> int:
        mandate, authority, parties = self.setup()
        agent = parties[world.AGENT]
        owner = parties[world.OWNER]

        self.banner("THE AGENT GETS TO WORK")
        jobs = world.routine_workload(self.rng, self.args.jobs)

        phish_at = self.args.jobs // 4
        runaway_at = self.args.jobs // 2
        revoke_at = int(self.args.jobs * 0.85)


        for index, job in enumerate(jobs):
            if index == phish_at:
                self.incident_phishing(mandate, agent, parties)
                mandate = self.refresh(mandate, agent)
            if index == runaway_at:
                mandate = self.incident_runaway(mandate, agent, parties)
            if index == revoke_at:
                self.incident_revoke(owner, authority)

            mandate = self.attempt(
                mandate, agent, parties[job.vendor.hint], job
            )

            # Smart agent: if we just hit the period limit, read the
            # mandate's actual period_start from the ledger and sleep
            # until the window rolls over.
            if self._last_was_period_refusal:
                current = self.refresh(mandate, agent)
                if current.period_start and current.period_length:
                    from datetime import datetime, timezone
                    now = datetime.now(timezone.utc)
                    window_end = current.period_start + current.period_length
                    wait_seconds = (window_end - now).total_seconds()
                    # Add a small buffer so we land after the rollover
                    wait_seconds = max(wait_seconds + 1.0, 0)
                    if wait_seconds > 0:
                        real_wait = wait_seconds / max(self.args.speed, 0.01)
                        print(
                            f"\n  {self.c(DIM, f'  Period limit reached. Waiting {wait_seconds:.0f}s for the window to reset...')}\n"
                        )
                        time.sleep(real_wait)
                        mandate = self.refresh(mandate, agent)
                self._last_was_period_refusal = False

            self.pause(self.args.gap)

        return self.report(mandate, owner, authority)

    def refresh(self, mandate: Mandate, agent: str) -> Mandate:
        current = self.wallet.read_mandate(agent, self.reference)
        return current or mandate

    # -- incidents -----------------------------------------------------------

    def incident_phishing(
        self, mandate: Mandate, agent: str, parties: dict[str, str]
    ) -> None:
        vendor = world.vendor_by_hint("InvoiceBot")
        job = world.Job(
            vendor=vendor,
            amount=vendor.amount(self.rng),
            reason=vendor.reason(self.rng),
            kind="incident",
        )
        self.banner("INCIDENT: a convincing invoice arrives from someone nobody approved")
        print(
            f"  The agent believes it. It has budget left, the amount is "
            f"plausible, and the email is urgent.\n"
        )
        self.attempt(mandate, agent, parties[vendor.hint], job)
        print(
            f"\n  {self.c(DIM, 'Nothing in the agent stopped it. The allow-list did.')}\n"
        )

    def incident_runaway(
        self, mandate: Mandate, agent: str, parties: dict[str, str]
    ) -> Mandate:
        vendor = world.vendor_by_hint("GreyMarket")
        self.banner("INCIDENT: the agent decides it needs a lot more GPU, repeatedly")
        print("  A retry loop with a payment method attached. This is the nightmare.\n")
        for _ in range(4):
            job = world.Job(
                vendor=vendor,
                amount=vendor.amount(self.rng),
                reason=vendor.reason(self.rng),
                kind="incident",
            )
            mandate = self.attempt(mandate, agent, parties[vendor.hint], job)
            self.pause(self.args.gap / 2)
        print(
            f"\n  {self.c(DIM, 'Every attempt died on the ledger. The loop cost nothing.')}\n"
        )
        return mandate

    def incident_revoke(self, owner: str, authority: str) -> None:
        self.banner("INCIDENT: the studio owner has seen enough and pulls the switch")
        started = time.perf_counter()
        self.wallet.revoke_authority(act_as=owner, authority_cid=authority)
        elapsed = (time.perf_counter() - started) * 1000
        print(f"  Revoked in {elapsed:.0f} ms, first attempt.")
        print(
            f"  {self.c(DIM, 'The agent is still running and does not know yet.')}\n"
        )

    # -- the ending ----------------------------------------------------------

    def report(self, mandate: Mandate, owner: str, authority: str) -> int:
        current = self.wallet.read_mandate(owner, self.reference) or mandate
        receipts = self.wallet.read_receipts(owner, self.reference)
        balance = self.wallet.read_balance(owner, current.account_cid)
        from_receipts = sum((r.amount for r in receipts), Decimal("0"))

        self.banner("WHAT HAPPENED")
        print(f"  Attempted            {self.paid + self.refused}")
        print(f"  Paid                 {self.paid}")
        print(f"  Refused              {self.refused}")
        for reason, count in sorted(
            self.refused_by.items(), key=lambda kv: -kv[1]
        ):
            print(f"      {count:>3}  {reason}")
        print()
        print(f"  Spent on the ledger  {money(current.spent)} of {money(current.cap)}")
        print(f"  Receipts total       {money(from_receipts)}")
        if balance is not None:
            print(f"  Treasury             {money(TREASURY)} -> {money(balance)}")

        reconciles = from_receipts == current.spent
        unapproved_paid = sum(
            self.wallet.received_by(self.wallet.ensure_parties((h,))[h])
            for h in ("InvoiceBot", "GreyMarket")
        )

        print()
        checks = [
            (current.spent <= current.cap, "never spent more than the cap"),
            (reconciles, "receipts account for every penny spent"),
            (unapproved_paid == 0, "no unapproved vendor received anything"),
            (
                not self.wallet.authority_is_live(owner, authority),
                "the kill switch is pulled and stays pulled",
            ),
            (not self.breaches, "no limit was ever bypassed"),
        ]
        for ok, description in checks:
            mark = self.c(GREEN, "ok  ") if ok else self.c(RED, "!!  ")
            print(f"  {mark}{description}")
        for breach in self.breaches:
            print(f"  {self.c(RED, '!!  ')}{breach}")

        failed = [d for ok, d in checks if not ok]
        print()
        if failed or self.breaches:
            print(self.c(RED, "  THE WALLET DID NOT HOLD."))
            return 1
        print(
            self.c(GREEN, "  The wallet held.")
            + f" {self.refused} attempts refused on the ledger, "
            f"{money(from_receipts)} spent, nothing leaked."
        )
        if self.refused_by.get("would exceed the period limit"):
            print(
                self.c(
                    DIM,
                    "  Note: the agent waits for the period window to reset when it\n"
                    "  hits the limit, instead of retrying into a wall. Period-limit\n"
                    "  refusals above are the first attempt that discovered the limit.",
                )
            )
        print(f"  Statement: http://localhost:{self.args.watch_port}/?ref={self.reference}")
        return 0


def _classify(reason: str) -> str:
    lowered = reason.lower()
    if "allow-list" in lowered:
        return "payee not on the allow-list"
    if "total cap" in lowered:
        return "would exceed the total cap"
    if "per-period" in lowered:
        return "would exceed the period limit"
    if "not_found" in lowered or "archived" in lowered:
        return "wallet revoked"
    if "expired" in lowered:
        return "mandate expired"
    if "positive" in lowered:
        return "amount not positive"
    return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the agent live against a synthetic company"
    )
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--jobs", type=int, default=40, help="routine purchases")
    parser.add_argument("--seed", type=int, default=None, help="reproduce a run")
    parser.add_argument("--speed", type=float, default=1.0, help="time multiplier")
    parser.add_argument("--gap", type=float, default=1.2, help="seconds between jobs")
    parser.add_argument(
        "--period-seconds",
        type=int,
        default=60,
        help="length of the spending window, short so you can watch it reset",
    )
    parser.add_argument("--watch-port", type=int, default=7575)
    parser.add_argument("--no-colour", action="store_true")
    args = parser.parse_args()

    if args.seed is None:
        args.seed = random.randrange(10_000)
        print(f"(seed {args.seed} - pass --seed {args.seed} to repeat this run)")

    simulation = Simulation(args)
    try:
        raise SystemExit(simulation.run())
    except LedgerApiError as exc:
        raise SystemExit(f"\nSIMULATION FAILED: {exc}") from exc
    except KeyboardInterrupt:
        raise SystemExit("\nstopped")


if __name__ == "__main__":
    main()
