"""Subprocess boundary for the authoritative spend-wallet CLI.

The demo layer invokes ordinary ``wallet_cli.py`` subcommands and renders their
JSON. It never imports ledger code and contains no cap, expiry, allow-list, or
revocation policy. The optional fixture is a fixed ordered UI replay only.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Sequence


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CLI_PATH = PACKAGE_ROOT / "scripts" / "wallet_cli.py"
DEFAULT_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "story.json"

MODE_LABELS = (
    "LIVE DEVNET",
    "LOCAL CANTON SANDBOX",
    "OFFLINE FIXTURE",
)
LEDGER_MODE_LABELS = MODE_LABELS[:2]
MACHINE_MODE_LABELS = {
    "DEVNET": "LIVE DEVNET",
    "SANDBOX": "LOCAL CANTON SANDBOX",
}
FIXTURE_WARNING = "UI DEVELOPMENT ONLY — NOT LEDGER ENFORCEMENT"

READ_ONLY_COMMANDS = {"health", "list-parties", "status", "audit"}
MUTATING_COMMANDS = {
    "upload-dar",
    "setup-demo",
    "create-mandate",
    "charge",
    "revoke",
    "run-demo",
}
KNOWN_COMMANDS = READ_ONLY_COMMANDS | MUTATING_COMMANDS


class DemoClientError(Exception):
    """A stable boundary failure intended for a user-facing error screen."""

    def __init__(
        self,
        category: str,
        message: str,
        remedy: str,
        mode_label: str,
        *,
        outcome_unknown: bool = False,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.remedy = remedy
        self.mode_label = mode_label
        self.outcome_unknown = outcome_unknown

    @property
    def title(self) -> str:
        if self.category == "LEDGER_TIMEOUT" or self.outcome_unknown:
            return "LEDGER UNAVAILABLE / OUTCOME UNKNOWN"
        if self.category in {"CLI_MISSING", "CLI_NOT_READABLE"}:
            return "ENVIRONMENT NOT READY / NO LEDGER RESULT"
        if self.category in {"LEDGER_UNAVAILABLE", "CLI_FAILED"}:
            return "LEDGER UNAVAILABLE / NO LEDGER RESULT"
        return "CLI PROTOCOL ERROR / NO LEDGER RESULT"

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode_label,
            "ok": False,
            "error": {
                "title": self.title,
                "code": self.category,
                "message": self.message,
                "remedy": self.remedy,
                "outcome": "UNKNOWN" if self.outcome_unknown else "NO_LEDGER_RESULT",
            },
        }


class WalletCli:
    """Invoke the DevNet-owned CLI one ordinary subcommand at a time."""

    def __init__(
        self,
        *,
        expected_mode: str,
        cli_path: Path | None = None,
        timeout_seconds: float = 20.0,
        fixture_path: Path | None = None,
    ) -> None:
        if expected_mode not in MODE_LABELS:
            raise ValueError(f"unsupported expected mode: {expected_mode}")
        self.expected_mode = expected_mode
        self.cli_path = (cli_path or DEFAULT_CLI_PATH).resolve()
        self.timeout_seconds = timeout_seconds
        self.fixture = (
            FixtureReplay(fixture_path or DEFAULT_FIXTURE_PATH)
            if expected_mode == "OFFLINE FIXTURE"
            else None
        )

    def preflight(self) -> dict[str, Any]:
        return self.run_json("health")

    def run_json(
        self,
        command: str,
        arguments: Sequence[str] = (),
    ) -> dict[str, Any]:
        if command not in KNOWN_COMMANDS:
            raise ValueError(f"unsupported wallet CLI command: {command}")
        argv = [str(value) for value in arguments]
        if self.fixture is not None:
            return self.fixture.run(command, argv)
        completed = self._run_process(command, argv, capture_output=True)
        assert completed.stdout is not None
        stdout = _decode_utf8(completed.stdout, "stdout", self.expected_mode)
        response = _parse_json_object(stdout, self.expected_mode)
        response = _map_and_validate_mode(response, self.expected_mode)
        if completed.returncode != 0:
            if command == "charge" and _is_definitive_ledger_rejection(response):
                return _normalize_ledger_rejection(response)
            category, message = _read_cli_error(response, completed.returncode)
            raise DemoClientError(
                category,
                message,
                "Check the selected ledger environment and CLI diagnostics, then run "
                "health again. The demo never falls back to a fixture.",
                self.expected_mode,
                outcome_unknown=command in MUTATING_COMMANDS,
            )
        if response.get("ok") is not True:
            raise DemoClientError(
                "CLI_PROTOCOL_ERROR",
                "wallet_cli.py exited successfully but did not set ok to true.",
                "Align the command with docs/CLI_CONTRACT.md.",
                self.expected_mode,
            )
        return response

    def run_demo_passthrough(self) -> int:
        """Hand terminal control to the CLI-owned P0 run-demo command."""
        if self.fixture is not None:
            raise DemoClientError(
                "FIXTURE_ONLY",
                "OFFLINE FIXTURE has no authoritative CLI run-demo command.",
                "Use the fixture development story in demo/run_demo.py, or select a "
                "configured ledger mode for the final pitch.",
                self.expected_mode,
            )
        completed = self._run_process("run-demo", [], capture_output=False)
        return completed.returncode

    def _run_process(
        self,
        command: str,
        arguments: Sequence[str],
        *,
        capture_output: bool,
    ) -> subprocess.CompletedProcess[bytes]:
        self._require_cli()
        process_argv = [sys.executable, str(self.cli_path), command, *arguments]
        try:
            if capture_output:
                return subprocess.run(
                    process_argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=self.timeout_seconds,
                    check=False,
                    shell=False,
                    cwd=PACKAGE_ROOT,
                    env=os.environ.copy(),
                )
            return subprocess.run(
                process_argv,
                check=False,
                shell=False,
                cwd=PACKAGE_ROOT,
                env=os.environ.copy(),
            )
        except subprocess.TimeoutExpired as exc:
            raise DemoClientError(
                "LEDGER_TIMEOUT",
                f"wallet_cli.py {command} did not respond within "
                f"{self.timeout_seconds:g} seconds.",
                "Do not retry a mutation automatically. Run status and audit first, "
                "then decide whether a new submission is safe.",
                self.expected_mode,
                outcome_unknown=command in MUTATING_COMMANDS,
            ) from exc
        except OSError as exc:
            raise DemoClientError(
                "CLI_FAILED",
                f"wallet_cli.py could not be started ({exc.__class__.__name__}).",
                "Verify the Python environment and CLI path, then run health again.",
                self.expected_mode,
                outcome_unknown=command in MUTATING_COMMANDS,
            ) from exc

    def _require_cli(self) -> None:
        if not self.cli_path.exists():
            raise DemoClientError(
                "CLI_MISSING",
                f"The authoritative wallet CLI was not found at {self.cli_path}.",
                "Integrate scripts/wallet_cli.py and docs/CLI_CONTRACT.md from the "
                "DevNet owner, configure the ledger, then rerun health.",
                self.expected_mode,
            )
        if not self.cli_path.is_file() or not os.access(self.cli_path, os.R_OK):
            raise DemoClientError(
                "CLI_NOT_READABLE",
                f"The authoritative wallet CLI is not readable at {self.cli_path}.",
                "Restore read access to scripts/wallet_cli.py, then rerun health.",
                self.expected_mode,
            )


class FixtureReplay:
    """Return fixed story snapshots without evaluating any submitted policy."""

    def __init__(self, fixture_path: Path) -> None:
        self.fixture_path = fixture_path.resolve()
        try:
            document = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise DemoClientError(
                "FIXTURE_INVALID",
                f"The UI fixture could not be loaded from {self.fixture_path}.",
                "Restore demo/fixtures/story.json before using fixture development mode.",
                "OFFLINE FIXTURE",
            ) from exc
        if not isinstance(document, dict) or not isinstance(document.get("steps"), list):
            raise DemoClientError(
                "FIXTURE_INVALID",
                "The UI fixture does not contain a valid fixed step list.",
                "Restore demo/fixtures/story.json before using fixture development mode.",
                "OFFLINE FIXTURE",
            )
        self.document = document
        self.steps: list[dict[str, Any]] = document["steps"]
        self.position = -1
        self.run_number = 0
        self.current_response = {
            "mode": "OFFLINE FIXTURE",
            "ok": True,
            "command": "status",
            "result": {
                "kind": "fixture_result",
                "outcome": "SIMULATED",
                "code": "FIXTURE_EMPTY",
                "message": "No canned story step has been replayed; no ledger was contacted.",
            },
            "state": copy.deepcopy(document.get("initial_state", {})),
        }

    def run(self, command: str, arguments: list[str]) -> dict[str, Any]:
        if command == "health":
            response = copy.deepcopy(self.current_response)
            response["command"] = "health"
            response["result"] = {
                "kind": "fixture_result",
                "outcome": "SIMULATED",
                "code": "FIXTURE_READY",
                "message": "Static UI replay loaded; no ledger was contacted.",
            }
            return response
        if command in {"status", "audit"}:
            response = copy.deepcopy(self.current_response)
            response["command"] = command
            response["result"] = {
                "kind": "fixture_result",
                "outcome": "SIMULATED",
                "code": "FIXTURE_SNAPSHOT",
                "message": "Replayed canned UI state; no ledger was contacted.",
            }
            return response

        if command == "setup-demo":
            self.position = -1
            self.run_number += 1

        next_position = self.position + 1
        if next_position >= len(self.steps):
            return self._sequence_mismatch(command, "The fixed story is complete.")
        step = self._materialize(self.steps[next_position])
        request = step.get("request", {})
        if request.get("command") != command or request.get("arguments") != arguments:
            expected = request.get("command", "unknown")
            return self._sequence_mismatch(
                command,
                f"The static replay expected {expected} next; it did not evaluate the request.",
            )

        self.position = next_position
        snapshot_name = step.get("snapshot")
        snapshots = self.document.get("snapshots", {})
        if not isinstance(snapshot_name, str) or not isinstance(snapshots, dict):
            return self._sequence_mismatch(
                command, "The fixed replay references an invalid canned snapshot."
            )
        state = self._materialize(snapshots.get(snapshot_name, {}))
        self.current_response = {
            "mode": "OFFLINE FIXTURE",
            "ok": True,
            "command": command,
            "result": copy.deepcopy(step.get("result", {})),
            "state": copy.deepcopy(state),
        }
        return copy.deepcopy(self.current_response)

    def _sequence_mismatch(self, command: str, message: str) -> dict[str, Any]:
        response = copy.deepcopy(self.current_response)
        response["command"] = command
        response["result"] = {
            "kind": "fixture_result",
            "outcome": "NOT_REPLAYED",
            "code": "FIXTURE_SEQUENCE_MISMATCH",
            "message": message,
        }
        return response

    def _materialize(self, value: Any) -> Any:
        marker = f"{self.run_number:03d}"
        if isinstance(value, str):
            return value.replace("{{RUN}}", marker)
        if isinstance(value, list):
            return [self._materialize(item) for item in value]
        if isinstance(value, dict):
            return {key: self._materialize(item) for key, item in value.items()}
        return value


def _decode_utf8(value: bytes, stream_name: str, mode_label: str) -> str:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DemoClientError(
            "CLI_PROTOCOL_ERROR",
            f"wallet_cli.py returned non-UTF-8 data on {stream_name}.",
            "Make every subcommand emit one UTF-8 JSON object on stdout and keep "
            "diagnostics on stderr.",
            mode_label,
        ) from exc


def _parse_json_object(stdout: str, mode_label: str) -> dict[str, Any]:
    if not stdout.strip():
        raise DemoClientError(
            "CLI_PROTOCOL_ERROR",
            "wallet_cli.py returned no JSON object.",
            "Make the subcommand emit one JSON object on stdout.",
            mode_label,
        )
    try:
        response = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise DemoClientError(
            "CLI_PROTOCOL_ERROR",
            "wallet_cli.py stdout was not exactly one valid JSON object.",
            "Keep diagnostics on stderr and follow docs/CLI_CONTRACT.md.",
            mode_label,
        ) from exc
    if not isinstance(response, dict):
        raise DemoClientError(
            "CLI_PROTOCOL_ERROR",
            "wallet_cli.py returned JSON that was not an object.",
            "Return the object shape documented in docs/CLI_CONTRACT.md.",
            mode_label,
        )
    return response


def _map_and_validate_mode(
    response: dict[str, Any], expected_mode: str
) -> dict[str, Any]:
    machine_mode = response.get("mode")
    actual_mode = MACHINE_MODE_LABELS.get(machine_mode)
    if actual_mode is None:
        raise DemoClientError(
            "CLI_PROTOCOL_ERROR",
            "wallet_cli.py returned an unknown machine mode.",
            "Return DEVNET or SANDBOX as documented in docs/CLI_CONTRACT.md.",
            expected_mode,
        )
    if actual_mode != expected_mode:
        raise DemoClientError(
            "MODE_MISMATCH",
            f"wallet_cli.py returned {actual_mode}, but the operator expected {expected_mode}.",
            "Select the intended environment explicitly and rerun health. The demo "
            "will not relabel or silently switch environments.",
            expected_mode,
        )
    mapped = copy.deepcopy(response)
    mapped["machineMode"] = machine_mode
    mapped["mode"] = actual_mode
    return mapped


def _read_cli_error(payload: dict[str, Any], returncode: int) -> tuple[str, str]:
    default = (
        "CLI_FAILED",
        f"wallet_cli.py exited with status {returncode} without a usable ledger result.",
    )
    error = payload.get("error")
    result = payload.get("result")
    source = error if isinstance(error, dict) else result
    if not isinstance(source, dict):
        return default
    code = source.get("category") or source.get("code")
    message = source.get("message")
    if not isinstance(code, str) or not isinstance(message, str):
        return default
    known = {
        "CONFIGURATION_MISSING",
        "CONFIGURATION_INVALID",
        "LEDGER_UNAVAILABLE",
        "LEDGER_TIMEOUT",
        "AUTHENTICATION_FAILED",
        "PACKAGE_NOT_DEPLOYED",
    }
    category = code if code in known else "CLI_FAILED"
    return category, " ".join(message.replace("\x00", "").split())[:500]


def _is_definitive_ledger_rejection(response: dict[str, Any]) -> bool:
    """Recognize only CLI evidence that Canton definitively rejected Charge."""
    error = response.get("error")
    if not isinstance(error, dict):
        return False
    category = error.get("category")
    if category in {
        "USAGE_ERROR",
        "INVALID_AMOUNT",
        "AUTH_CONFIGURATION",
        "AUTH_RESPONSE",
        "AUTH_UNREACHABLE",
        "LEDGER_UNREACHABLE",
        "AUTHENTICATION",
        "AUTHORIZATION",
        "NON_LOCAL_PARTY",
        "LOCAL_PARTY_NOT_FOUND",
        "CAN_ACT_AS_MISSING",
        "DEMO_NOT_SETUP",
        "MANDATE_NOT_FOUND",
        "INTERNAL_ERROR",
    }:
        return False
    http_status = error.get("httpStatus")
    if http_status in {401, 403}:
        return False
    assertion = error.get("assertion")
    if isinstance(assertion, str) and assertion:
        return True
    ledger_code = error.get("ledgerCode")
    definite_answer = error.get("definiteAnswer")
    return (
        isinstance(http_status, int)
        and 400 <= http_status < 500
        and isinstance(ledger_code, str)
        and bool(ledger_code)
        and definite_answer is True
    )


def _normalize_ledger_rejection(response: dict[str, Any]) -> dict[str, Any]:
    """Expose a proven policy rejection as a renderable ledger result."""
    normalized = copy.deepcopy(response)
    error = normalized.get("error")
    assert isinstance(error, dict)
    normalized["ledgerRejected"] = True
    normalized["result"] = {
        "kind": "ledger_result",
        "outcome": "REJECTED",
        "code": error.get("category", "LEDGER_REJECTED"),
        "message": error.get("message", "The ledger rejected the request."),
        "httpStatus": error.get("httpStatus"),
        "ledgerCode": error.get("ledgerCode"),
        "definiteAnswer": error.get("definiteAnswer"),
        "assertion": error.get("assertion"),
    }
    return normalized
