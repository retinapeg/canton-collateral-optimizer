from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest
from unittest import mock


DEMO_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DEMO_DIR))

from presentation import build_view, normalize_cli_snapshot, terminal_screen  # noqa: E402
import run_demo  # noqa: E402
from wallet_client import DemoClientError, WalletCli  # noqa: E402


class WalletCliContractTests(unittest.TestCase):
    def test_machine_modes_map_to_required_display_labels(self) -> None:
        for machine_mode, display_mode in (
            ("SANDBOX", "LOCAL CANTON SANDBOX"),
            ("DEVNET", "LIVE DEVNET"),
        ):
            with self.subTest(machine_mode=machine_mode), self._fake_cli(
                success_script(machine_mode)
            ) as cli_path:
                client = WalletCli(expected_mode=display_mode, cli_path=cli_path)
                response = client.run_json("health")
                self.assertEqual(response["machineMode"], machine_mode)
                self.assertEqual(response["mode"], display_mode)

    def test_unknown_machine_mode_fails_closed(self) -> None:
        with self._fake_cli(success_script("MYSTERY")) as cli_path:
            client = WalletCli(
                expected_mode="LOCAL CANTON SANDBOX", cli_path=cli_path
            )
            with self.assertRaises(DemoClientError) as raised:
                client.run_json("health")
        self.assertEqual(raised.exception.category, "CLI_PROTOCOL_ERROR")

    def test_mode_mismatch_fails_closed(self) -> None:
        with self._fake_cli(success_script("DEVNET")) as cli_path:
            client = WalletCli(
                expected_mode="LOCAL CANTON SANDBOX", cli_path=cli_path
            )
            with self.assertRaises(DemoClientError) as raised:
                client.run_json("health")
        self.assertEqual(raised.exception.category, "MODE_MISMATCH")

    def test_run_demo_uses_long_timeout_while_quick_commands_stay_short(self) -> None:
        response = json.dumps(
            {
                "schemaVersion": 1,
                "ok": True,
                "command": "health",
                "mode": "SANDBOX",
                "result": {},
            }
        ).encode("utf-8")
        with self._fake_cli("pass\n") as cli_path:
            client = WalletCli(
                expected_mode="LOCAL CANTON SANDBOX",
                cli_path=cli_path,
                timeout_seconds=7,
                run_demo_timeout_seconds=300,
            )
            with mock.patch("wallet_client.subprocess.run") as run:
                run.return_value = mock.Mock(returncode=0, stdout=response, stderr=b"")
                client.run_json("health")
                self.assertEqual(run.call_args.kwargs["timeout"], 7)
                run.return_value = mock.Mock(
                    returncode=0,
                    stdout=response.replace(b'"health"', b'"run-demo"'),
                    stderr=b"",
                )
                client.run_json("run-demo")
                self.assertEqual(run.call_args.kwargs["timeout"], 300)

    def test_all_malicious_charge_arguments_reach_cli_once_unchanged(self) -> None:
        cases = (
            (
                ("--counterparty", "Merchant-A", "--amount", "80", "--memo", "attack-80"),
                "charge would exceed the cap",
                "DAML_FAILURE",
            ),
            (
                ("--counterparty", "Merchant-B", "--amount", "10", "--memo", "attack-b"),
                "counterparty is not allow-listed",
                "DAML_FAILURE",
            ),
            (
                ("--counterparty", "Merchant-A", "--amount", "1", "--memo", "stale-1"),
                None,
                "CONTRACT_NOT_FOUND",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "calls.jsonl"
            cli_path = Path(directory) / "wallet_cli.py"
            cli_path.write_text(rejection_recording_script(), encoding="utf-8")
            client = WalletCli(
                expected_mode="LOCAL CANTON SANDBOX", cli_path=cli_path
            )
            with mock.patch.dict(os.environ, {"FAKE_CALL_LOG": str(log_path)}):
                for arguments, assertion, ledger_code in cases:
                    response = client.run_json("charge", arguments)
                    self.assertTrue(response["ledgerRejected"])
                    self.assertEqual(response["result"]["outcome"], "REJECTED")
                    self.assertEqual(response["result"]["ledgerCode"], ledger_code)
                    if assertion is not None:
                        self.assertEqual(response["result"]["assertion"], assertion)
            calls = [json.loads(line) for line in log_path.read_text().splitlines()]
        self.assertEqual(
            calls,
            [["charge", *arguments] for arguments, _, _ in cases],
        )

    def test_transport_failure_is_not_called_a_ledger_rejection(self) -> None:
        source = textwrap.dedent(
            """
            import json
            print(json.dumps({
                "schemaVersion": 1,
                "ok": False,
                "command": "charge",
                "mode": "SANDBOX",
                "error": {
                    "category": "LEDGER_UNREACHABLE",
                    "message": "The ledger could not be reached."
                }
            }))
            raise SystemExit(1)
            """
        )
        with self._fake_cli(source) as cli_path:
            client = WalletCli(
                expected_mode="LOCAL CANTON SANDBOX", cli_path=cli_path
            )
            with self.assertRaises(DemoClientError) as raised:
                client.run_json(
                    "charge",
                    ("--counterparty", "Merchant-A", "--amount", "80", "--memo", "x"),
                )
        self.assertTrue(raised.exception.outcome_unknown)
        self.assertNotIn("rejected", raised.exception.message.lower())

    def _fake_cli(self, source: str):
        return FakeCli(source)


class CanonicalProjectionTests(unittest.TestCase):
    def test_status_and_audit_envelopes_render_all_required_values(self) -> None:
        status = canonical_status_response()
        audit = canonical_audit_response()
        snapshot = normalize_cli_snapshot(status, audit)
        view = build_view(snapshot)
        wallet = view["wallet"]

        self.assertEqual(wallet["owner"], "Owner::sandbox")
        self.assertEqual(wallet["agent"], "Agent::sandbox")
        self.assertEqual(wallet["cap"], "100.0")
        self.assertEqual(wallet["spent"], "30.0")
        self.assertEqual(wallet["remaining"], "70.0")
        self.assertEqual(wallet["status"], "ACTIVE")
        self.assertEqual(wallet["allowed_counterparties"], ["Merchant-A::sandbox"])
        self.assertEqual(len(view["audit_records"]), 1)
        self.assertEqual(view["audit_records"][0]["amount"], "30.0")
        self.assertEqual(
            view["audit_records"][0]["recorded_at"], "2026-08-29T17:30:00Z"
        )
        screen = terminal_screen(snapshot)
        for value in (
            "Owner::sandbox",
            "100.0",
            "30.0",
            "70.0",
            "Merchant-A::sandbox",
            "2026-08-29T17:30:00Z",
        ):
            self.assertIn(value, screen)

    def test_authoritative_story_orders_revoke_before_stale_charge(self) -> None:
        screen = run_demo.authoritative_story_screen(canonical_run_demo_response())
        self.assertLess(
            screen.index("5. Owner revocation committed"),
            screen.index("6. Post-revocation Merchant-A / 1"),
        )
        self.assertIn("MODE: LOCAL CANTON SANDBOX", screen)
        self.assertIn("AUDIT COUNT        1", screen)

    def test_missing_cli_is_clear_and_has_no_fixture_fallback(self) -> None:
        missing = Path("/tmp/definitely-not-present-wallet-cli.py")
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = run_demo.main(["--cli", str(missing)])
        text = output.getvalue()
        self.assertEqual(exit_code, 2)
        self.assertIn("MODE: LOCAL CANTON SANDBOX", text)
        self.assertIn("NO LEDGER RESULT", text)
        self.assertIn("There is no automatic fallback", text)
        self.assertNotIn("Traceback", text)

    def test_fixture_is_explicit_and_never_calls_its_output_a_ledger_result(self) -> None:
        client = WalletCli(expected_mode="OFFLINE FIXTURE")
        client.run_json("setup-demo")
        response = client.run_json("create-mandate")
        screen = terminal_screen(response)
        self.assertIn("OFFLINE FIXTURE", screen)
        self.assertIn("SIMULATED UI RESPONSE — NO LEDGER", screen)
        self.assertNotIn("REJECTED BY LEDGER", screen)

    def test_fixture_reset_uses_a_new_run_scope_and_one_current_record(self) -> None:
        client = WalletCli(expected_mode="OFFLINE FIXTURE")
        first = run_fixture_to_valid_charge(client)
        second = run_fixture_to_valid_charge(client)
        first_records = first["state"]["audit_records"]
        second_records = second["state"]["audit_records"]
        self.assertEqual(len(first_records), 1)
        self.assertEqual(len(second_records), 1)
        self.assertNotEqual(first_records[0]["record_id"], second_records[0]["record_id"])


class FakeCli:
    def __init__(self, source: str) -> None:
        self.source = source
        self.temporary: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self) -> Path:
        self.temporary = tempfile.TemporaryDirectory()
        path = Path(self.temporary.name) / "wallet_cli.py"
        path.write_text(self.source, encoding="utf-8")
        return path

    def __exit__(self, *unused: object) -> None:
        assert self.temporary is not None
        self.temporary.cleanup()


def success_script(machine_mode: str) -> str:
    return textwrap.dedent(
        f"""
        import json
        import sys
        command = sys.argv[1]
        print(json.dumps({{
            "schemaVersion": 1,
            "ok": True,
            "command": command,
            "mode": {machine_mode!r},
            "result": {{}}
        }}))
        """
    )


def rejection_recording_script() -> str:
    return textwrap.dedent(
        """
        import json
        import os
        from pathlib import Path
        import sys

        arguments = sys.argv[1:]
        with Path(os.environ["FAKE_CALL_LOG"]).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(arguments) + "\\n")
        amount = arguments[arguments.index("--amount") + 1]
        counterparty = arguments[arguments.index("--counterparty") + 1]
        if amount == "1":
            code = "CONTRACT_NOT_FOUND"
            assertion = None
        elif counterparty == "Merchant-B":
            code = "DAML_FAILURE"
            assertion = "counterparty is not allow-listed"
        else:
            code = "DAML_FAILURE"
            assertion = "charge would exceed the cap"
        error = {
            "category": code,
            "message": "The ledger rejected the request.",
            "httpStatus": 400,
            "ledgerCode": code,
            "definiteAnswer": True,
        }
        if assertion:
            error["assertion"] = assertion
        print(json.dumps({
            "schemaVersion": 1,
            "ok": False,
            "command": "charge",
            "mode": "SANDBOX",
            "error": error,
        }))
        raise SystemExit(1)
        """
    )


def canonical_status_response() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "ok": True,
        "command": "status",
        "mode": "LOCAL CANTON SANDBOX",
        "machineMode": "SANDBOX",
        "result": {
            "parties": {
                "owner": "Owner::sandbox",
                "agent": "Agent::sandbox",
                "merchantA": "Merchant-A::sandbox",
                "merchantB": "Merchant-B::sandbox",
            },
            "mandateReference": "wallet-demo-001",
            "activeMandate": {
                "contractId": "00-mandate-after-30",
                "owner": "Owner::sandbox",
                "agent": "Agent::sandbox",
                "cap": "100.0",
                "spent": "30.0",
                "expiresAt": "2026-08-30T17:00:00Z",
                "allowedCounterparties": ["Merchant-A::sandbox"],
                "mandateReference": "wallet-demo-001",
                "remainingAllowance": "70.0",
            },
            "activeProposalCount": 0,
            "revoked": False,
        },
    }


def run_fixture_to_valid_charge(client: WalletCli) -> dict[str, object]:
    client.run_json("setup-demo")
    client.run_json("create-mandate")
    return client.run_json(
        "charge",
        (
            "--counterparty",
            "Merchant-A",
            "--amount",
            "30",
            "--memo",
            "Approved Merchant-A purchase",
        ),
    )


def canonical_audit_response() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "ok": True,
        "command": "audit",
        "mode": "LOCAL CANTON SANDBOX",
        "machineMode": "SANDBOX",
        "result": {
            "mandateReference": "wallet-demo-001",
            "count": 1,
            "records": [
                {
                    "contractId": "00-audit-001",
                    "owner": "Owner::sandbox",
                    "agent": "Agent::sandbox",
                    "counterparty": "Merchant-A::sandbox",
                    "amount": "30.0",
                    "transactionTime": "2026-08-29T17:30:00Z",
                    "memo": "approved demo charge",
                    "mandateReference": "wallet-demo-001",
                    "previousSpent": "0.0",
                    "newSpent": "30.0",
                    "cap": "100.0",
                    "remainingAllowance": "70.0",
                }
            ],
        },
    }


def canonical_run_demo_response() -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "ok": True,
        "command": "run-demo",
        "mode": "LOCAL CANTON SANDBOX",
        "machineMode": "SANDBOX",
        "result": {
            "parties": {
                "owner": "Owner::sandbox",
                "agent": "Agent::sandbox",
                "merchantA": "Merchant-A::sandbox",
                "merchantB": "Merchant-B::sandbox",
            },
            "mandate": {
                "cap": "100.0",
                "expiresAt": "2026-08-30T17:00:00Z",
                "allowedCounterparties": ["Merchant-A::sandbox"],
            },
            "successfulCharge": {
                "updateId": "update-30",
                "auditContractId": "00-audit-001",
            },
            "statusAfterCharge": canonical_status_response()["result"],
            "deliberateRejections": [
                {
                    "attack": "cap_exceeded",
                    "rejected": True,
                    "rawCategory": {
                        "ledgerCode": "DAML_FAILURE",
                        "assertion": "charge would exceed the cap",
                        "definiteAnswer": True,
                    },
                },
                {
                    "attack": "counterparty_not_allowed",
                    "rejected": True,
                    "rawCategory": {
                        "ledgerCode": "DAML_FAILURE",
                        "assertion": "counterparty is not allow-listed",
                        "definiteAnswer": True,
                    },
                },
                {
                    "attack": "revoked_mandate",
                    "rejected": True,
                    "rawCategory": {
                        "ledgerCode": "CONTRACT_NOT_FOUND",
                        "definiteAnswer": True,
                    },
                },
            ],
            "revocation": {"updateId": "update-revoke"},
            "finalAudit": canonical_audit_response()["result"],
            "proof": {
                "successfulChargeCount": 1,
                "successfulAmount": "30",
                "remainingBeforeRevocation": "70",
                "failedChargesCreatedAuditRecords": False,
            },
        },
    }


if __name__ == "__main__":
    unittest.main()
