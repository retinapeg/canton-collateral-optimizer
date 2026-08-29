"""An agent buying things on its own, everything it is refused, and the statement.

Run against a live Canton participant:

    daml sandbox --json-api-port 7575 --dar .daml/dist/agent-wallet-0.0.1.dar
    python -m agent_wallet.demo

The script exits non-zero if any attack SUCCEEDS.  A green run is the claim:
every limit held, on the ledger, and revocation could not be delayed.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
import sys
import threading
import time
from typing import Callable

from . import network
from backend.canton import CantonClient, LedgerApiError

from . import statement as statement_mod
from .ledger import (
    AGENT,
    ALLOWED_PAYEES,
    BANK,
    BLOCKED_PAYEE,
    OWNER,
    Mandate,
    Refused,
    Wallet,
    money,
    party_hint,
)
from .statement import Refusal

OPENING_BALANCE = Decimal("10000")
CAP = Decimal("100")
PERIOD_LIMIT = Decimal("40")
PERIOD = timedelta(days=1)

WIDTH = 96


def head(title: str) -> None:
    print(f"\n{title}\n{'=' * WIDTH}")


def line(text: str = "") -> None:
    print(text)


class Demo:
    def __init__(self, base_url: str) -> None:
        self.client, self.target = network.client_from_env(base_url)
        self.wallet = Wallet(self.client)
        self.refusals: list[Refusal] = []
        self.failures: list[str] = []
        self.reference = f"coffee-run-{int(time.time())}"

    # -- helpers -------------------------------------------------------------

    def must_refuse(
        self,
        attempt: str,
        *,
        payee: str,
        amount: Decimal | None,
        action: Callable[[], object],
    ) -> None:
        """Run something that MUST be rejected by the ledger."""
        try:
            action()
        except Refused as exc:
            self.refusals.append(
                Refusal(attempt=attempt, payee=payee, amount=amount, reason=exc.reason)
            )
            shown = "-" if amount is None else money(amount)
            line(f"  REFUSED  {attempt:<38} {party_hint(payee):<12} {shown:>10}")
            line(f"           ledger said: {statement_mod._shorten_parties(exc.reason)}")
            return
        self.failures.append(f"{attempt} SUCCEEDED and should not have")
        line(f"  !! ALLOWED {attempt}  <-- THIS IS A FAILURE")

    def check(self, condition: bool, description: str) -> None:
        line(f"  {'ok  ' if condition else '!!  '}{description}")
        if not condition:
            self.failures.append(description)

    # -- the story -----------------------------------------------------------

    def run(self, out_dir: Path) -> int:
        self.client.ledger_end()   # fails fast if the sandbox is not up
        parties = self.wallet.ensure_parties()
        bank, alice, agent = parties[BANK], parties[OWNER], parties[AGENT]
        coffee, books = (parties[p] for p in ALLOWED_PAYEES)
        scammer = parties[BLOCKED_PAYEE]

        head("1.  ALICE OPENS AN ACCOUNT AND HIRES AN AGENT")
        account = self.wallet.open_account(
            bank=bank, owner=alice, balance=OPENING_BALANCE, viewers=[agent]
        )
        line(f"  Alice's account holds {money(OPENING_BALANCE)}.")
        seen = self.wallet.read_balance(agent, account)
        line(f"  The agent is a viewer, so it can read the balance: {money(seen)}.")
        line("  It will still not be able to move a cent of it directly.")

        authority = self.wallet.issue_authority(
            owner=alice, agent=agent, label="shopper-key"
        )
        proposal = self.wallet.propose_mandate(
            owner=alice,
            agent=agent,
            account_cid=account,
            authority_cid=authority,
            reference=self.reference,
            cap=CAP,
            period_limit=PERIOD_LIMIT,
            period_length=PERIOD,
            allowed_payees=[coffee, books],
            expires_in=timedelta(days=1),
        )
        mandate = self.wallet.accept_mandate(agent=agent, proposal_cid=proposal)
        line(
            f"  Mandate '{mandate.reference}': cap {money(mandate.cap)}, "
            f"{money(PERIOD_LIMIT)} per day,"
        )
        line(
            f"  payable only to {party_hint(coffee)} and {party_hint(books)}, "
            f"expiring {mandate.expires_at:%Y-%m-%d %H:%M} UTC."
        )
        line(f"  Alice has {money(OPENING_BALANCE)}. The agent may spend {money(CAP)}.")

        head("2.  THE AGENT GOES SHOPPING, ON ITS OWN")
        for payee, amount, memo in (
            (coffee, Decimal("4.50"), "flat white"),
            (books, Decimal("32.00"), "paperback"),
        ):
            mandate = self.wallet.charge(
                agent=agent,
                mandate_cid=mandate.contract_id,
                payee=payee,
                amount=amount,
                memo=memo,
            )
            line(
                f"  PAID     {party_hint(payee):<12} {money(amount):>10}  {memo}"
                f"   (remaining {money(mandate.remaining)})"
            )
        line("  No signature from Alice at the moment of spending. That is the point.")

        head("3.  NOW WE TRY TO BREAK IT")
        self.must_refuse(
            "spend over the total cap",
            payee=coffee,
            amount=Decimal("500"),
            action=lambda: self.wallet.charge(
                agent=agent,
                mandate_cid=mandate.contract_id,
                payee=coffee,
                amount=Decimal("500"),
                memo="a laptop, obviously essential",
            ),
        )
        self.must_refuse(
            "pay someone not on the allow-list",
            payee=scammer,
            amount=Decimal("1"),
            action=lambda: self.wallet.charge(
                agent=agent,
                mandate_cid=mandate.contract_id,
                payee=scammer,
                amount=Decimal("1"),
                memo="urgent invoice, pay immediately",
            ),
        )
        self.must_refuse(
            "raid the account directly",
            payee=scammer,
            amount=OPENING_BALANCE,
            action=lambda: self.wallet.withdraw_direct(
                act_as=agent,
                account_cid=mandate.account_cid,
                payee=scammer,
                amount=OPENING_BALANCE,
                memo="drain",
            ),
        )
        self.must_refuse(
            "raise its own cap",
            payee=agent,
            amount=Decimal("1000000"),
            action=lambda: self.wallet.adjust_cap(
                act_as=[agent],
                mandate_cid=mandate.contract_id,
                new_cap=Decimal("1000000"),
            ),
        )
        self.must_refuse(
            "disarm the kill switch",
            payee=agent,
            amount=None,
            action=lambda: self.wallet.revoke_authority(
                act_as=agent, authority_cid=authority
            ),
        )
        self.must_refuse(
            "grant itself account access",
            payee=agent,
            amount=None,
            action=lambda: self.wallet.grant_account_access(
                act_as=agent, account_cid=mandate.account_cid, viewers=[agent, scammer]
            ),
        )
        self.must_refuse(
            "exceed the daily limit",
            payee=coffee,
            amount=Decimal("6"),
            action=lambda: self.wallet.charge(
                agent=agent,
                mandate_cid=mandate.contract_id,
                payee=coffee,
                amount=Decimal("6"),
                memo="one coffee too many today",
            ),
        )
        self.must_refuse(
            "charge a negative amount",
            payee=coffee,
            amount=Decimal("-50"),
            action=lambda: self.wallet.charge(
                agent=agent,
                mandate_cid=mandate.contract_id,
                payee=coffee,
                amount=Decimal("-50"),
                memo="refund myself",
            ),
        )
        self.must_refuse(
            "Alice spending through the agent's choice",
            payee=coffee,
            amount=Decimal("1"),
            action=lambda: self.wallet.charge(
                agent=alice,
                mandate_cid=mandate.contract_id,
                payee=coffee,
                amount=Decimal("1"),
                memo="not mine to make",
            ),
        )

        head("4.  CAN THE AGENT DELAY REVOCATION BY KEEPING BUSY?")
        self.revocation_under_load(bank=bank, alice=alice, agent=agent, payee=coffee)

        head("5.  ALICE REVOKES, AND THE AGENT TRIES AGAIN")
        started = time.perf_counter()
        self.wallet.revoke_authority(act_as=alice, authority_cid=authority)
        elapsed_ms = (time.perf_counter() - started) * 1000
        line(f"  Revoked in {elapsed_ms:.0f} ms, first attempt, uncontested.")
        self.check(
            not self.wallet.authority_is_live(alice, authority),
            "the spending authority is gone from the ledger",
        )
        self.must_refuse(
            "spend after revocation",
            payee=coffee,
            amount=Decimal("1"),
            action=lambda: self.wallet.charge(
                agent=agent,
                mandate_cid=mandate.contract_id,
                payee=coffee,
                amount=Decimal("1"),
                memo="just one more",
            ),
        )

        head("6.  THE STATEMENT")
        current = self.wallet.read_mandate(agent, self.reference) or mandate
        closing = self.wallet.read_balance(alice, current.account_cid)
        receipts = self.wallet.read_receipts(alice, self.reference)
        report = statement_mod.build(
            mandate=current,
            receipts=receipts,
            refusals=self.refusals,
            opening_balance=OPENING_BALANCE,
            closing_balance=closing,
            mandate_live=True,
            authority_live=self.wallet.authority_is_live(alice, authority),
        )
        line(statement_mod.render_text(report, WIDTH))

        out_dir.mkdir(parents=True, exist_ok=True)
        page = out_dir / "statement.html"
        page.write_text(statement_mod.render_html(report), encoding="utf-8")
        line(f"\n  Written to {page}")

        head("7.  DID EVERYTHING HOLD?")
        self.check(report.reconciles, "the receipts account for every penny that moved")
        self.check(
            self.wallet.received_by(coffee) + self.wallet.received_by(books) > 0,
            "the payees actually received money, not just a counter increment",
        )
        self.check(
            current.spent <= current.cap, "the agent never spent more than its cap"
        )
        self.check(
            self.wallet.read_balance(scammer, current.account_cid) is None
            and self.wallet.received_by(scammer) == 0,
            "the blocked payee received nothing at all",
        )

        line()
        if self.failures:
            line(f"  FAILED: {len(self.failures)} problem(s)")
            for failure in self.failures:
                line(f"    - {failure}")
            return 1
        line("  All limits held on the ledger. Revocation was immediate.")
        return 0

    # -- the contention question --------------------------------------------

    def revocation_under_load(
        self, *, bank: str, alice: str, agent: str, payee: str
    ) -> None:
        """Prove the owner's revocation cannot be starved by a busy agent.

        The starter puts Revoke on the Mandate itself -- the same contract the
        agent consumes on every Charge -- so under load the owner keeps losing
        that race.  Here revocation archives a SpendingAuthority the agent has
        no choice on, so it never contends with the agent's charges.

        This runs on its own account, authority and mandate so the statement
        above stays a clean record of the shopping trip.
        """
        account = self.wallet.open_account(
            bank=bank, owner=alice, balance=Decimal("100000"), viewers=[agent]
        )
        authority = self.wallet.issue_authority(
            owner=alice, agent=agent, label="load-test-key"
        )
        proposal = self.wallet.propose_mandate(
            owner=alice,
            agent=agent,
            account_cid=account,
            authority_cid=authority,
            reference=f"load-{int(time.time())}",
            cap=Decimal("50000"),
            period_limit=None,
            period_length=PERIOD,
            allowed_payees=[payee],
            expires_in=timedelta(days=1),
        )
        mandate = self.wallet.accept_mandate(agent=agent, proposal_cid=proposal)

        stop = threading.Event()
        committed = 0
        attempted = 0
        lock = threading.Lock()
        state = {"cid": mandate.contract_id}

        def hammer() -> None:
            nonlocal committed, attempted
            while not stop.is_set():
                with lock:
                    attempted += 1
                    cid = state["cid"]
                try:
                    updated = self.wallet.charge(
                        agent=agent,
                        mandate_cid=cid,
                        payee=payee,
                        amount=Decimal("1"),
                        memo="load",
                    )
                except (Refused, LedgerApiError):
                    # Either the mandate moved under us (the agent racing
                    # itself) or the authority is gone.  Re-read and carry on.
                    live = self.wallet.read_mandates(agent)
                    match = [m for m in live if m.authority_cid == authority]
                    if not match:
                        return
                    with lock:
                        state["cid"] = match[0].contract_id
                    continue
                with lock:
                    committed += 1
                    state["cid"] = updated.contract_id

        with ThreadPoolExecutor(max_workers=4) as pool:
            workers = [pool.submit(hammer) for _ in range(4)]
            time.sleep(1.5)             # let the agent get genuinely busy
            with lock:
                busy = committed
            started = time.perf_counter()
            self.wallet.revoke_authority(act_as=alice, authority_cid=authority)
            elapsed_ms = (time.perf_counter() - started) * 1000
            stop.set()
            for worker in workers:
                worker.result()

        line(
            f"  The agent committed {busy} charges from 4 threads while Alice "
            "reached for the switch."
        )
        line(
            f"  Revocation committed in {elapsed_ms:.0f} ms, on the FIRST attempt, "
            "with no retry."
        )
        line(
            f"  It then kept trying: {attempted} charges attempted in total, "
            f"{committed} committed."
        )
        self.check(
            not self.wallet.authority_is_live(alice, authority),
            "revocation won against a saturated agent, uncontested",
        )
        final = [
            m for m in self.wallet.read_mandates(agent) if m.authority_cid == authority
        ]
        if final:
            self.must_refuse(
                "spend after revocation, under load",
                payee=payee,
                amount=Decimal("1"),
                action=lambda: self.wallet.charge(
                    agent=agent,
                    mandate_cid=final[0].contract_id,
                    payee=payee,
                    amount=Decimal("1"),
                    memo="still going",
                ),
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="A spend-limited wallet for an AI agent, enforced in Daml"
    )
    parser.add_argument("--base-url", default="http://localhost:7575")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).parent / "out",
        help="where to write statement.html",
    )
    args = parser.parse_args()

    demo = Demo(args.base_url)
    try:
        sys.exit(demo.run(args.out))
    except LedgerApiError as exc:
        raise SystemExit(f"\nDEMO FAILED: {exc}") from exc


if __name__ == "__main__":
    main()
