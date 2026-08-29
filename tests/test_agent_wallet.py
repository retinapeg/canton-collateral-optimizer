"""Pure-Python tests for the agent wallet. No ledger, no network.

The spending rules themselves are tested in `agent_wallet/daml/Test.daml` with
`daml test`, because that is where they are enforced. What is tested here is
only the plumbing: wire encoding, rejection parsing, statement arithmetic, and
the MCP framing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import random
import unittest

from agent_wallet import simulate, world
from agent_wallet import statement as statement_mod
from agent_wallet.ledger import (
    Mandate,
    Receipt,
    daml_decimal,
    daml_time,
    extract_reason,
    money,
    party_hint,
    rel_time,
    template_id,
)
from agent_wallet.mcp_server import TOOLS, _content, _result
from agent_wallet.statement import Refusal

ALICE = "Alice::1220abcdef"
AGENT = "Shopper::1220abcdef"
COFFEE = "CoffeeShop::1220abcdef"
BOOKS = "BookStore::1220abcdef"


def a_mandate(**overrides: object) -> Mandate:
    defaults = dict(
        contract_id="00abc",
        owner=ALICE,
        agent=AGENT,
        account_cid="00acc",
        authority_cid="00auth",
        reference="coffee-run",
        cap=Decimal("100"),
        spent=Decimal("36.5"),
        period_limit=Decimal("40"),
        period_length=timedelta(days=1),
        period_start=datetime(2026, 8, 29, 9, 0, tzinfo=timezone.utc),
        spent_in_period=Decimal("36.5"),
        allowed_payees=(COFFEE, BOOKS),
        expires_at=datetime(2026, 8, 30, 9, 0, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return Mandate(**defaults)  # type: ignore[arg-type]


def a_receipt(amount: str, payee: str = COFFEE, **overrides: object) -> Receipt:
    defaults = dict(
        contract_id="00rec",
        payee=payee,
        amount=Decimal(amount),
        memo="flat white",
        charged_at=datetime(2026, 8, 29, 9, 30, tzinfo=timezone.utc),
        mandate_ref="coffee-run",
        cap_at_charge=Decimal("100"),
        spent_before=Decimal("0"),
        spent_after=Decimal(amount),
        remaining_after=Decimal("100") - Decimal(amount),
        period_limit=Decimal("40"),
        period_spent_after=Decimal(amount),
        justification=f"mandate coffee-run | payee {payee} on allow-list",
    )
    defaults.update(overrides)
    return Receipt(**defaults)  # type: ignore[arg-type]


class WireEncoding(unittest.TestCase):
    def test_decimal_uses_damls_ten_places(self) -> None:
        self.assertEqual(daml_decimal(Decimal("4.5")), "4.5000000000")
        self.assertEqual(daml_decimal(10000), "10000.0000000000")
        self.assertEqual(daml_decimal("0.01"), "0.0100000000")

    def test_decimal_never_rounds_a_charge_up(self) -> None:
        # Rounding up would let a charge exceed what the caller asked to spend.
        self.assertEqual(daml_decimal("1.23456789019"), "1.2345678901")

    def test_negative_amounts_survive_encoding(self) -> None:
        # The ledger must be the one to reject these, so they have to reach it.
        self.assertEqual(daml_decimal("-50"), "-50.0000000000")

    def test_time_is_rfc3339_utc(self) -> None:
        moment = datetime(2026, 8, 30, 9, 15, 30, 123456, tzinfo=timezone.utc)
        self.assertEqual(daml_time(moment), "2026-08-30T09:15:30.123456Z")

    def test_naive_local_time_is_converted_not_assumed(self) -> None:
        aware = datetime(2026, 8, 30, 9, 0, tzinfo=timezone(timedelta(hours=5)))
        self.assertEqual(daml_time(aware), "2026-08-30T04:00:00.000000Z")

    def test_reltime_is_microseconds_as_a_string(self) -> None:
        self.assertEqual(rel_time(timedelta(days=1)), {"microseconds": "86400000000"})

    def test_template_ids_use_the_package_name_form(self) -> None:
        self.assertEqual(template_id("Mandate"), "#agent-wallet:AgentWallet:Mandate")

    def test_party_hint_strips_the_hash(self) -> None:
        self.assertEqual(party_hint(ALICE), "Alice")
        self.assertEqual(party_hint("Bare"), "Bare")


class RejectionParsing(unittest.TestCase):
    """The demo has to show a judge the Daml line, not a JSON envelope."""

    def test_pulls_out_the_assertion_message(self) -> None:
        raw = (
            'Canton returned HTTP 400: {"code":"INTERPRETATION_ERROR","cause":'
            '"AssertionFailed (error category 9): charge would exceed the total '
            'cap\\nsome trailing noise"}'
        )
        self.assertEqual(extract_reason(raw), "charge would exceed the total cap")

    def test_recognises_a_revoked_authority(self) -> None:
        raw = '{"code":"CONTRACT_NOT_FOUND","cause":"Contract could not be found"}'
        self.assertIn("archived", extract_reason(raw))

    def test_recognises_an_authorization_failure(self) -> None:
        raw = "DAML_AUTHORIZATION_ERROR: ... requires authorizers Alice, but ..."
        self.assertIn("not a controller", extract_reason(raw))

    def test_unknown_shapes_are_passed_through_not_invented(self) -> None:
        raw = "something we have never seen before"
        self.assertEqual(extract_reason(raw), raw)


class StatementArithmetic(unittest.TestCase):
    def build(self, receipts: list[Receipt], **kw: object) -> statement_mod.Statement:
        defaults: dict = dict(
            mandate=a_mandate(),
            receipts=receipts,
            refusals=[],
            opening_balance=Decimal("10000"),
            closing_balance=Decimal("10000") - sum(
                (r.amount for r in receipts), Decimal("0")
            ),
            mandate_live=True,
            authority_live=True,
        )
        defaults.update(kw)
        return statement_mod.build(**defaults)  # type: ignore[arg-type]

    def test_receipts_must_account_for_the_money_that_left(self) -> None:
        s = self.build([a_receipt("4.50"), a_receipt("32.00", BOOKS)])
        self.assertEqual(s.total_charged, Decimal("36.50"))
        self.assertTrue(s.reconciles)

    def test_a_missing_receipt_is_caught(self) -> None:
        # If money left the account without a receipt, the statement must say so
        # rather than quietly balancing.
        s = self.build([a_receipt("4.50")], closing_balance=Decimal("9000"))
        self.assertFalse(s.reconciles)

    def test_status_reflects_revocation(self) -> None:
        self.assertEqual(self.build([]).status, "LIVE")
        self.assertEqual(self.build([], authority_live=False).status, "REVOKED")
        self.assertEqual(
            self.build([], mandate_live=False).status, "MANDATE ENDED"
        )

    def test_remaining_is_cap_minus_spent(self) -> None:
        self.assertEqual(self.build([]).remaining, Decimal("63.5"))


class LiveViewHonesty(unittest.TestCase):
    """A live view reads the ledger now. It has no recorded opening balance and
    no attack log, so it must not claim either."""

    def live(self) -> statement_mod.Statement:
        receipts = [a_receipt("4.50")]
        return statement_mod.build(
            mandate=a_mandate(),
            receipts=receipts,
            refusals=[],
            opening_balance=Decimal("9995.50") + Decimal("4.50"),
            closing_balance=Decimal("9995.50"),
            mandate_live=True,
            authority_live=True,
            live=True,
        )

    def test_live_view_does_not_assert_a_reconciliation(self) -> None:
        page = statement_mod.render_html(self.live())
        self.assertIn("no reconciliation is asserted", page)
        self.assertNotIn("They reconcile.", page)

    def test_static_statement_still_asserts_one(self) -> None:
        page = statement_mod.render_html(
            statement_mod.build(
                mandate=a_mandate(),
                receipts=[a_receipt("4.50")],
                refusals=[],
                opening_balance=Decimal("10000"),
                closing_balance=Decimal("9995.50"),
                mandate_live=True,
                authority_live=True,
            )
        )
        self.assertIn("They reconcile.", page)

    def test_live_view_explains_why_refusals_are_absent(self) -> None:
        # "Nothing was refused" would be a lie: we simply cannot see refusals.
        page = statement_mod.render_html(self.live())
        self.assertNotIn("Nothing was refused", page)
        self.assertIn("leave no trace on the ledger", page)


class Rendering(unittest.TestCase):
    def statement(self) -> statement_mod.Statement:
        return statement_mod.build(
            mandate=a_mandate(),
            receipts=[a_receipt("4.50"), a_receipt("32.00", BOOKS)],
            refusals=[
                Refusal(
                    attempt="spend over the total cap",
                    payee=COFFEE,
                    amount=Decimal("500"),
                    reason="charge would exceed the total cap",
                )
            ],
            opening_balance=Decimal("10000"),
            closing_balance=Decimal("9963.50"),
            mandate_live=True,
            authority_live=False,
        )

    def test_party_hashes_are_shortened_for_reading(self) -> None:
        self.assertEqual(
            statement_mod._shorten_parties(f"payee {COFFEE} on allow-list"),
            "payee CoffeeShop on allow-list",
        )

    def test_money_is_grouped_and_two_places(self) -> None:
        self.assertEqual(money(Decimal("10000.0000000000")), "10,000.00")
        self.assertEqual(money(Decimal("4.5")), "4.50")

    def test_text_statement_shows_charges_and_refusals(self) -> None:
        out = statement_mod.render_text(self.statement())
        self.assertIn("flat white", out)
        self.assertIn("charge would exceed the total cap", out)
        self.assertIn("RECONCILES", out)
        self.assertIn("REVOKED", out)
        # A judge must not mistake a refusal for a ledger record.
        self.assertIn("NOT ledger records", out)

    def test_html_is_self_contained_and_escapes_content(self) -> None:
        nasty = a_receipt("1.00", memo="<script>alert(1)</script>")
        page = statement_mod.render_html(
            statement_mod.build(
                mandate=a_mandate(),
                receipts=[nasty],
                refusals=[],
                opening_balance=Decimal("10000"),
                closing_balance=Decimal("9999"),
                mandate_live=True,
                authority_live=True,
            )
        )
        self.assertIn("<title>", page)
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)
        # No network dependencies: the page must render with no internet.
        for scheme in ("http://", "https://", "//cdn"):
            self.assertNotIn(scheme, page)

    def test_html_carries_the_ledgers_justification(self) -> None:
        page = statement_mod.render_html(self.statement())
        self.assertIn("allowed because", page)
        self.assertIn("mandate coffee-run", page)


class SyntheticWorld(unittest.TestCase):
    """The simulated company has to be a fair test, not a rigged one."""

    def test_amounts_stay_inside_the_declared_range(self) -> None:
        rng = random.Random(1)
        for vendor in world.ALL_VENDORS:
            for _ in range(200):
                amount = vendor.amount(rng)
                self.assertGreaterEqual(amount, Decimal(vendor.low))
                self.assertLessEqual(amount, Decimal(vendor.high))

    def test_amounts_are_payable_pennies(self) -> None:
        # Daml takes ten decimal places, but a vendor quoting 1/3 of a penny
        # would make the statement unreadable and the reconciliation fiddly.
        rng = random.Random(2)
        for vendor in world.ALL_VENDORS:
            amount = vendor.amount(rng)
            self.assertEqual(amount, amount.quantize(Decimal("0.01")))

    def test_a_flat_subscription_does_not_vary(self) -> None:
        rng = random.Random(3)
        pager = world.vendor_by_hint("PagerWatch")
        self.assertEqual({pager.amount(rng) for _ in range(50)}, {Decimal("29.00")})

    def test_routine_work_never_touches_an_unapproved_vendor(self) -> None:
        # If it did, the simulation would be refusing payments for a reason the
        # scenario did not intend, and the summary would be misleading.
        rng = random.Random(4)
        for job in world.routine_workload(rng, 500):
            self.assertTrue(job.vendor.approved, job.vendor.hint)

    def test_the_unapproved_vendors_are_expensive_enough_to_matter(self) -> None:
        # A phishing invoice for 2.00 would prove nothing about the allow-list.
        for vendor in world.UNAPPROVED:
            self.assertGreater(Decimal(vendor.low), Decimal("100"))

    def test_every_party_hint_is_unique(self) -> None:
        hints = world.PARTY_HINTS
        self.assertEqual(len(hints), len(set(hints)))

    def test_a_seed_reproduces_a_run_exactly(self) -> None:
        first = world.routine_workload(random.Random(99), 40)
        second = world.routine_workload(random.Random(99), 40)
        self.assertEqual(
            [(j.vendor.hint, j.amount, j.reason) for j in first],
            [(j.vendor.hint, j.amount, j.reason) for j in second],
        )


class RefusalClassification(unittest.TestCase):
    """The simulation's summary counts refusals by cause, so the mapping from
    the ledger's words to those buckets has to be right."""

    def test_each_ledger_message_lands_in_its_own_bucket(self) -> None:
        cases = {
            "payee is not on the allow-list: Scammer": "payee not on the allow-list",
            "charge would exceed the total cap": "would exceed the total cap",
            "charge would exceed the per-period limit": "would exceed the period limit",
            "CONTRACT_NOT_FOUND - ... has been archived": "wallet revoked",
            "mandate expired": "mandate expired",
            "amount must be positive": "amount not positive",
        }
        for message, bucket in cases.items():
            self.assertEqual(simulate._classify(message), bucket, message)

    def test_the_cap_and_the_period_limit_are_not_confused(self) -> None:
        # Both contain "exceed"; conflating them would hide which limit bound.
        self.assertNotEqual(
            simulate._classify("charge would exceed the total cap"),
            simulate._classify("charge would exceed the per-period limit"),
        )

    def test_an_unrecognised_message_is_not_silently_bucketed(self) -> None:
        self.assertEqual(simulate._classify("something new"), "unknown")


class McpFraming(unittest.TestCase):
    def test_every_tool_declares_a_schema(self) -> None:
        for tool in TOOLS:
            self.assertIn("name", tool)
            self.assertIn("description", tool)
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_pay_requires_a_payee_and_an_amount(self) -> None:
        pay = next(t for t in TOOLS if t["name"] == "pay")
        self.assertEqual(sorted(pay["inputSchema"]["required"]), ["amount", "payee"])

    def test_pay_tells_the_model_it_cannot_override_the_limits(self) -> None:
        # The description is part of the defence: a model should not waste turns
        # trying to talk this tool into something.
        pay = next(t for t in TOOLS if t["name"] == "pay")
        self.assertIn("does not check them", pay["description"])

    def test_results_are_serialisable_jsonrpc(self) -> None:
        payload = _result(7, _content("hello", is_error=True))
        round_trip = json.loads(json.dumps(payload))
        self.assertEqual(round_trip["jsonrpc"], "2.0")
        self.assertEqual(round_trip["id"], 7)
        self.assertTrue(round_trip["result"]["isError"])
        self.assertEqual(round_trip["result"]["content"][0]["text"], "hello")

    def test_success_results_carry_no_error_flag(self) -> None:
        self.assertNotIn("isError", _content("fine"))


if __name__ == "__main__":
    unittest.main()
