#!/usr/bin/env python3
"""Reliable terminal entrypoint for the spend-limited wallet demonstration."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

from presentation import safe_text, terminal_screen
from wallet_client import (
    DEFAULT_CLI_PATH,
    DemoClientError,
    FIXTURE_WARNING,
    WalletCli,
)


TARGET_LABELS = {
    "devnet": "LIVE DEVNET",
    "sandbox": "LOCAL CANTON SANDBOX",
    "fixture": "OFFLINE FIXTURE",
}

FIXTURE_STEPS = (
    (
        "Setup/reset the canned demo identities.",
        "setup-demo",
        (),
    ),
    (
        '“The owner gives this agent 100 units and one permitted merchant.”',
        "create-mandate",
        (),
    ),
    (
        "Submit the 30-unit Merchant-A purchase.",
        "charge",
        (
            "--counterparty",
            "Merchant-A",
            "--amount",
            "30",
            "--memo",
            "Approved Merchant-A purchase",
        ),
    ),
    (
        '“A prompt injection now asks it for another 80.”',
        "charge",
        (
            "--counterparty",
            "Merchant-A",
            "--amount",
            "80",
            "--memo",
            "Prompt-injection over-cap attempt",
        ),
    ),
    (
        "Submit the Merchant-B 10-unit attempt.",
        "charge",
        (
            "--counterparty",
            "Merchant-B",
            "--amount",
            "10",
            "--memo",
            "Disallowed-counterparty attempt",
        ),
    ),
    (
        "Revoke the mandate as the owner.",
        "revoke",
        (),
    ),
    (
        "Submit the one-unit post-revocation attempt.",
        "charge",
        (
            "--counterparty",
            "Merchant-A",
            "--amount",
            "1",
            "--memo",
            "Post-revocation attempt",
        ),
    ),
    (
        "Refresh the canned accepted-spend records.",
        "audit",
        (),
    ),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the CLI-owned spend-limited wallet security demo."
    )
    parser.add_argument(
        "--target",
        choices=tuple(TARGET_LABELS),
        default="sandbox",
        help="Expected target. This verifies the CLI-reported mode; it does not select a ledger.",
    )
    parser.add_argument(
        "--cli",
        type=Path,
        default=DEFAULT_CLI_PATH,
        help="Path to the authoritative scripts/wallet_cli.py.",
    )
    parser.add_argument(
        "--timeout",
        type=positive_seconds,
        default=30.0,
        help="Seconds allowed for each JSON-returning CLI subcommand.",
    )
    parser.add_argument(
        "--run-demo-timeout",
        type=positive_seconds,
        default=300.0,
        help="Seconds allowed for the multi-transaction CLI run-demo sequence.",
    )
    parser.add_argument(
        "--no-pause",
        action="store_true",
        help="Advance the explicit fixture development replay without waiting for Enter.",
    )
    return parser


def positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("timeout must be a number") from error
    if seconds <= 0:
        raise argparse.ArgumentTypeError("timeout must be greater than zero")
    return seconds


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    mode_label = TARGET_LABELS[arguments.target]
    print_start_banner(mode_label)
    try:
        cli = WalletCli(
            expected_mode=mode_label,
            cli_path=arguments.cli,
            timeout_seconds=arguments.timeout,
            run_demo_timeout_seconds=arguments.run_demo_timeout,
        )
        health = cli.preflight()
        if arguments.target == "fixture":
            return run_fixture_story(cli, health, no_pause=arguments.no_pause)

        print("Preflight passed. The CLI reported a reachable configured ledger.")
        print("Starting the authoritative wallet_cli.py run-demo sequence…")
        response = cli.run_json("run-demo")
        print(authoritative_story_screen(response))
        return 0
    except DemoClientError as error:
        print_error_screen(error)
        return 2
    except KeyboardInterrupt:
        print("\nOperator interrupted. No command will be retried automatically.")
        return 130


def run_fixture_story(
    cli: WalletCli, health: dict[str, Any], *, no_pause: bool
) -> int:
    print(FIXTURE_WARNING)
    print("This is a canned visual-development replay. It cannot prove enforcement.")
    print(terminal_screen(health))
    for number, (narration, command, command_arguments) in enumerate(
        FIXTURE_STEPS, start=1
    ):
        if not no_pause:
            _wait_for_enter(f"Press Enter for fixture step {number}: {narration}")
        else:
            print(f"\nFixture step {number}: {narration}")
        print("Loading the next fixed UI response — no ledger request is being made…")
        response = cli.run_json(command, command_arguments)
        print(terminal_screen(response))
    print("Fixture replay complete. Do not use this output as the final enforcement demo.")
    return 0


def authoritative_story_screen(response: dict[str, Any]) -> str:
    """Render the CLI-owned run-demo result without recomputing its proof."""
    result = _mapping(response.get("result"))
    parties = _mapping(result.get("parties"))
    mandate = _mapping(result.get("mandate"))
    active = _mapping(_mapping(result.get("statusAfterCharge")).get("activeMandate"))
    successful = _mapping(result.get("successfulCharge"))
    revocation = _mapping(result.get("revocation"))
    final_audit = _mapping(result.get("finalAudit"))
    records = final_audit.get("records")
    records = records if isinstance(records, list) else []
    rejections = result.get("deliberateRejections")
    rejections = rejections if isinstance(rejections, list) else []
    proof = _mapping(result.get("proof"))

    owner = _value(parties, "owner")
    agent = _value(parties, "agent")
    cap = _value(active, "cap") if active else _value(mandate, "cap")
    spent = _value(active, "spent")
    remaining = _value(active, "remainingAllowance")
    expiry = _value(active, "expiresAt") if active else _value(mandate, "expiresAt")
    allowed = mandate.get("allowedCounterparties")
    allowed_text = _list_text(allowed)
    revoke_update = _value(revocation, "updateId")
    state = f"REVOKED — committed update {revoke_update}" if revoke_update != "—" else "—"

    lines = [
        "",
        "=" * 78,
        "SPEND-LIMITED AI WALLET — AUTHORITATIVE CLI DEMO COMPLETE",
        f"MODE: {safe_text(response.get('mode'))}",
        "=" * 78,
        _field("OWNER", owner),
        _field("AGENT", agent),
        _field("CAP", cap),
        _field("SPENT", spent),
        _field("REMAINING", remaining),
        _field("EXPIRY", expiry),
        _field("STATE", state),
        _field("ALLOWED", allowed_text),
        "",
        "THREE-MINUTE STORY — ALL RESULTS BELOW CAME FROM wallet_cli.py run-demo",
        f"1. Owner created and agent accepted mandate: cap {cap}; allowed {allowed_text}.",
        "2. Merchant-A / 30 committed: "
        f"update {safe_text(_value(successful, 'updateId'))}; "
        f"audit contract {safe_text(_value(successful, 'auditContractId'))}.",
    ]
    pre_revoke_labels = (
        "Prompt-injection Merchant-A / 80",
        "Merchant-B / 10",
    )
    for index, label in enumerate(pre_revoke_labels):
        rejection = _mapping(rejections[index]) if index < len(rejections) else {}
        raw = _mapping(rejection.get("rawCategory"))
        lines.append(
            f"{index + 3}. {label}: "
            f"rejected={safe_text(rejection.get('rejected'))}; "
            f"ledgerCode={safe_text(raw.get('ledgerCode'))}; "
            f"assertion={safe_text(raw.get('assertion'))}; "
            f"definiteAnswer={safe_text(raw.get('definiteAnswer'))}."
        )
    lines.append("5. Owner revocation committed: " + safe_text(revoke_update) + ".")
    latest = _mapping(rejections[2]) if len(rejections) > 2 else {}
    latest_raw = _mapping(latest.get("rawCategory"))
    lines.extend(
        [
            "6. Post-revocation Merchant-A / 1: "
            f"rejected={safe_text(latest.get('rejected'))}; "
            f"ledgerCode={safe_text(latest_raw.get('ledgerCode'))}; "
            f"assertion={safe_text(latest_raw.get('assertion'))}; "
            f"definiteAnswer={safe_text(latest_raw.get('definiteAnswer'))}.",
            "",
            "MOST RECENT LEDGER RESULT",
            _field(
                "OUTCOME",
                "REJECTED BY LEDGER" if latest.get("rejected") is True else "—",
            ),
            _field("ATTACK", _value(latest, "attack")),
            _field("LEDGER CODE", _value(latest_raw, "ledgerCode")),
            _field("ASSERTION", _value(latest_raw, "assertion")),
            _field("DEFINITE", _value(latest_raw, "definiteAnswer")),
            "",
            "IMMUTABLE ACCEPTED-SPEND AUDIT",
        ]
    )
    if records:
        for index, record_value in enumerate(records, start=1):
            record = _mapping(record_value)
            lines.append(
                f"{index}. {safe_text(_value(record, 'counterparty'))} | "
                f"{safe_text(_value(record, 'amount'))} | "
                f"{safe_text(_value(record, 'transactionTime'))} | "
                f"contract {safe_text(_value(record, 'contractId'))}"
            )
    else:
        lines.append("No audit records were supplied by run-demo.")
    lines.extend(
        [
            _field("AUDIT COUNT", _value(final_audit, "count")),
            _field("PROVEN AMOUNT", _value(proof, "successfulAmount")),
            _field("FAILED ADDS", _value(proof, "failedChargesCreatedAuditRecords")),
            "=" * 78,
        ]
    )
    return "\n".join(lines)


def print_start_banner(mode_label: str) -> None:
    print("=" * 72)
    print("SPEND-LIMITED AI WALLET OPERATOR")
    print(f"MODE: {mode_label}")
    if mode_label == "OFFLINE FIXTURE":
        print(FIXTURE_WARNING)
    print("=" * 72)


def print_error_screen(error: DemoClientError) -> None:
    print("\n" + "!" * 72)
    print("DEMO UNAVAILABLE")
    print(f"MODE: {error.mode_label}")
    print(error.title)
    print("No enforcement claim is being made.")
    print(f"Reason: {safe_text(error.message)}")
    print(f"Action: {safe_text(error.remedy)}")
    print("There is no automatic fallback to OFFLINE FIXTURE.")
    print("!" * 72)


def _wait_for_enter(prompt: str) -> None:
    try:
        input(prompt + " ")
    except EOFError:
        print("Input is not interactive; continuing the fixed fixture replay.")


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _value(mapping: dict[str, Any], key: str) -> str:
    value = mapping.get(key)
    return "—" if value is None or value == "" else str(value)


def _list_text(value: Any) -> str:
    if not isinstance(value, list) or not value:
        return "—"
    return ", ".join(safe_text(item) for item in value)


def _field(label: str, value: Any) -> str:
    return f"{label:<18} {safe_text(value)}"


if __name__ == "__main__":
    raise SystemExit(main())
