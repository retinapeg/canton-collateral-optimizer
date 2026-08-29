"""Stateful presentation controller over the stateless wallet CLI subprocess."""

from __future__ import annotations

import copy
from typing import Any

from presentation import build_view, normalize_cli_snapshot
from wallet_client import DemoClientError, WalletCli


ACTION_PRESETS: dict[str, tuple[str, tuple[str, ...]]] = {
    "setup": ("setup-demo", ()),
    "create_accept": ("create-mandate", ()),
    "purchase_30": (
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
    "attack_80": (
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
    "attack_merchant_b": (
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
    "revoke": ("revoke", ()),
    "attack_after_revoke": (
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
}


class DashboardSession:
    """Serialize actions and retain only the last verified display snapshot."""

    def __init__(self, cli: WalletCli) -> None:
        self.cli = cli
        self.last_status: dict[str, Any] | None = None
        self.last_audit: dict[str, Any] | None = None
        self.last_snapshot: dict[str, Any] | None = None
        self.most_recent_result: dict[str, Any] | None = None
        self.state_stale = False

    def preflight_view(self) -> dict[str, Any]:
        response = self.cli.preflight()
        self.state_stale = False
        if self.cli.fixture is not None:
            self.last_snapshot = response
        return build_view(response, state_stale=False)

    def execute_action(self, action: str) -> dict[str, Any]:
        if action == "refresh":
            return self.refresh()
        if action not in ACTION_PRESETS:
            raise ValueError(f"unknown dashboard action: {action}")
        command, arguments = ACTION_PRESETS[action]
        try:
            response = self.cli.run_json(command, arguments)
            self.most_recent_result = _display_result(response)
            if self.cli.fixture is not None:
                self.last_snapshot = response
                self.state_stale = False
                return build_view(
                    response,
                    most_recent_result=self.most_recent_result,
                    state_stale=False,
                )
            if command == "setup-demo":
                self.last_snapshot = _setup_snapshot(response)
                self.last_status = None
                self.last_audit = None
                self.state_stale = False
                return build_view(
                    self.last_snapshot,
                    most_recent_result=self.most_recent_result,
                    state_stale=False,
                )
            return self._refresh_after(response)
        except DemoClientError:
            self.state_stale = True
            raise

    def refresh(self) -> dict[str, Any]:
        try:
            if self.cli.fixture is not None:
                response = self.cli.run_json("audit")
                self.last_snapshot = response
                self.state_stale = False
                return build_view(
                    response,
                    most_recent_result=self.most_recent_result,
                    state_stale=False,
                )
            return self._refresh_after(None)
        except DemoClientError:
            self.state_stale = True
            raise

    def _refresh_after(
        self, action_response: dict[str, Any] | None
    ) -> dict[str, Any]:
        previous_state = None
        if isinstance(self.last_snapshot, dict):
            candidate = self.last_snapshot.get("state")
            if isinstance(candidate, dict):
                previous_state = candidate
        status = self.cli.run_json("status")
        audit = self.cli.run_json("audit")
        snapshot = normalize_cli_snapshot(
            status,
            audit,
            action_response=action_response,
            previous_state=previous_state,
        )
        self.last_status = status
        self.last_audit = audit
        self.last_snapshot = snapshot
        self.state_stale = False
        return build_view(
            snapshot,
            most_recent_result=self.most_recent_result,
            state_stale=False,
        )

    def error_view(self) -> dict[str, Any] | None:
        if self.last_snapshot is None:
            return None
        return build_view(
            self.last_snapshot,
            most_recent_result=self.most_recent_result,
            state_stale=True,
        )


def _setup_snapshot(response: dict[str, Any]) -> dict[str, Any]:
    result = response.get("result")
    result = result if isinstance(result, dict) else {}
    parties = result.get("parties")
    parties = parties if isinstance(parties, dict) else {}
    return {
        "mode": response.get("mode"),
        "machineMode": response.get("machineMode"),
        "ok": True,
        "command": response.get("command", "setup-demo"),
        "result": copy.deepcopy(result),
        "state": {
            "owner": parties.get("owner"),
            "agent": parties.get("agent"),
            "cap": None,
            "spent": None,
            "remaining": None,
            "expiry": None,
            "status": "NOT CREATED",
            "allowed_counterparties": [],
            "audit_records": [],
        },
    }


def _display_result(response: dict[str, Any]) -> dict[str, Any]:
    result = response.get("result")
    result = copy.deepcopy(result) if isinstance(result, dict) else {}
    if result.get("kind") in {"ledger_result", "fixture_result"}:
        return result
    command = str(response.get("command", "command"))
    update_id = _first(
        result,
        "updateId",
        "acceptUpdateId",
        "proposalUpdateId",
        "transactionId",
    )
    is_ledger_mutation = command in {"create-mandate", "charge", "revoke"}
    return {
        "kind": "ledger_result" if is_ledger_mutation else "setup_result",
        "outcome": "ACCEPTED" if is_ledger_mutation else "COMPLETED",
        "code": command.upper().replace("-", "_"),
        "message": f"wallet_cli.py returned ok=true for {command}.",
        "transaction_id": update_id,
    }


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if mapping.get(key) is not None:
            return mapping[key]
    return None

