"""Read-only projection of CLI JSON into terminal/browser display fields."""

from __future__ import annotations

import json
import unicodedata
from typing import Any

from wallet_client import FIXTURE_WARNING


UNKNOWN = "—"


def normalize_cli_snapshot(
    status_response: dict[str, Any],
    audit_response: dict[str, Any],
    *,
    action_response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Combine canonical CLI read results without deriving wallet policy values."""
    status_result = _mapping(status_response.get("result"))
    audit_result = _mapping(audit_response.get("result"))
    active = _mapping(status_result.get("activeMandate"))
    parties = _mapping(status_result.get("parties"))

    if active:
        state = {
            "owner": _first(active, "owner", "ownerParty", "owner_party_id"),
            "agent": _first(active, "agent", "agentParty", "agent_party_id"),
            "cap": active.get("cap"),
            "spent": active.get("spent"),
            "remaining": active.get("remainingAllowance"),
            "expiry": active.get("expiresAt"),
            "status": "ACTIVE",
            "allowed_counterparties": active.get("allowedCounterparties", []),
            "mandate_reference": _first(
                active, "mandateReference", "mandate_reference"
            ),
            "mandate_contract_id": _first(active, "contractId", "contract_id"),
        }
    else:
        state = {
            "owner": _first(parties, "owner"),
            "agent": _first(parties, "agent"),
        }
        state["status"] = "REVOKED" if status_result.get("revoked") is True else "NOT ACTIVE"

    records = audit_result.get("records")
    state["audit_records"] = copy_records(records)
    state["audit_count"] = audit_result.get("count")
    state["audit_mandate_reference"] = audit_result.get("mandateReference")

    source = action_response or status_response
    return {
        "mode": source.get("mode") or status_response.get("mode") or audit_response.get("mode"),
        "machineMode": source.get("machineMode")
        or status_response.get("machineMode")
        or audit_response.get("machineMode"),
        "ok": True,
        "command": source.get("command", "status+audit"),
        "result": _mapping(source.get("result")),
        "state": state,
    }


def build_view(
    response: dict[str, Any],
    *,
    most_recent_result: dict[str, Any] | None = None,
    state_stale: bool = False,
) -> dict[str, Any]:
    """Select supplied fields without deriving balances or policy state."""
    state = _mapping(response.get("state"))
    canonical_result = _mapping(response.get("result"))
    if not state and isinstance(canonical_result.get("activeMandate"), dict):
        active = _mapping(canonical_result.get("activeMandate"))
        state = {
            "owner": _first(active, "owner", "ownerParty"),
            "agent": _first(active, "agent", "agentParty"),
            "cap": active.get("cap"),
            "spent": active.get("spent"),
            "remaining": active.get("remainingAllowance"),
            "expiry": active.get("expiresAt"),
            "status": "ACTIVE",
            "allowed_counterparties": active.get("allowedCounterparties", []),
        }
    wallet = _mapping(state.get("wallet")) or _mapping(response.get("wallet"))
    mandate = _mapping(state.get("mandate"))
    source = mandate or wallet or state
    identities = _mapping(state.get("identities"))

    owner = _first(
        source,
        "owner",
        "owner_party_id",
        "ownerParty",
        "ownerPartyId",
    )
    agent = _first(
        source,
        "agent",
        "agent_party_id",
        "agentParty",
        "agentPartyId",
    )
    if owner is None:
        owner = _identity_value(identities.get("owner"))
    if agent is None:
        agent = _identity_value(identities.get("agent"))

    allowed_raw = _first(
        source,
        "allowed_counterparties",
        "allowedCounterparties",
        "allowed_merchants",
    )
    allowed = _display_list(allowed_raw)

    records_raw = _first(
        state,
        "audit_records",
        "auditRecords",
        "audit",
        "records",
    )
    if records_raw is None:
        records_raw = _first(response, "audit_records", "auditRecords", "audit")
    if records_raw is None:
        records_raw = canonical_result.get("records")
    if isinstance(records_raw, dict):
        records_raw = _first(records_raw, "records", "audit_records", "auditRecords")
    records = records_raw if isinstance(records_raw, list) else []

    result = most_recent_result or _mapping(response.get("result"))
    return {
        "mode": _text(response.get("mode")),
        "state_stale": state_stale,
        "fixture_warning": FIXTURE_WARNING
        if response.get("mode") == "OFFLINE FIXTURE"
        else None,
        "wallet": {
            "owner": _display(owner),
            "agent": _display(agent),
            "cap": _display(_first(source, "cap", "spending_cap", "spendingCap")),
            "spent": _display(_first(source, "spent", "total_spent", "totalSpent")),
            "remaining": _display(
                _first(source, "remaining", "remaining_cap", "remainingCap")
            ),
            "expiry": _display(
                _first(source, "expiry", "expires_at", "expiresAt")
            ),
            "status": _display(
                _first(source, "status", "state", "mandate_status", "mandateStatus")
            ),
            "allowed_counterparties": allowed,
        },
        "audit_records": [_normalize_record(record) for record in records],
        "result": _normalize_result(result),
        "raw_command": _display(
            _first(response, "command", "action", "operation", "subcommand")
        ),
    }


def terminal_screen(
    response: dict[str, Any],
    *,
    most_recent_result: dict[str, Any] | None = None,
    state_stale: bool = False,
) -> str:
    view = build_view(
        response,
        most_recent_result=most_recent_result,
        state_stale=state_stale,
    )
    wallet = view["wallet"]
    lines = [
        "=" * 72,
        "SPEND-LIMITED AI WALLET",
        f"MODE: {safe_text(view['mode'])}",
    ]
    if view["fixture_warning"]:
        lines.append(FIXTURE_WARNING)
    if state_stale:
        lines.append("STATE STALE — refresh status and audit before trusting these values")
    lines.extend(
        [
            "=" * 72,
            _field("OWNER", wallet["owner"]),
            _field("AGENT", wallet["agent"]),
            _field("CAP", wallet["cap"]),
            _field("SPENT", wallet["spent"]),
            _field("REMAINING", wallet["remaining"]),
            _field("EXPIRY", wallet["expiry"]),
            _field("STATE", wallet["status"]),
            _field(
                "ALLOWED",
                ", ".join(safe_text(item) for item in wallet["allowed_counterparties"])
                or UNKNOWN,
            ),
            "",
            "SIMULATED UI RESPONSE — NO LEDGER"
            if view["mode"] == "OFFLINE FIXTURE"
            else "MOST RECENT LEDGER RESULT",
        ]
    )
    result = view["result"]
    lines.extend(
        [
            _field("OUTCOME", result["outcome"]),
            _field("CODE", result["code"]),
            _field("MESSAGE", result["message"]),
            _field("TRANSACTION", result["transaction_id"]),
            _field("OBSERVED", result["observed_at"]),
            "",
            "CANNED ACCEPTED-SPEND RECORDS — NOT A LEDGER AUDIT"
            if view["mode"] == "OFFLINE FIXTURE"
            else "IMMUTABLE ACCEPTED-SPEND AUDIT",
        ]
    )
    records = view["audit_records"]
    if not records:
        lines.append("No accepted-spend records supplied.")
    else:
        for index, record in enumerate(records, start=1):
            lines.append(
                f"{index}. {safe_text(record['counterparty'])} | "
                f"{safe_text(record['amount'])} | {safe_text(record['recorded_at'])} | "
                f"record {safe_text(record['record_id'])} | "
                f"transaction {safe_text(record['transaction_id'])}"
            )
    lines.append("=" * 72)
    return "\n".join(lines)


def safe_text(value: Any) -> str:
    text = _display(value)
    safe_characters: list[str] = []
    for character in text:
        category = unicodedata.category(character)
        if category.startswith("C") and character not in {"\t"}:
            safe_characters.append(" ")
        else:
            safe_characters.append(character)
    return " ".join("".join(safe_characters).split())


def _normalize_result(result: dict[str, Any]) -> dict[str, str]:
    return {
        "kind": _display(_first(result, "kind", "type")),
        "outcome": _display(_first(result, "outcome", "status")),
        "code": _display(_first(result, "code", "reason_code", "reasonCode")),
        "message": _display(_first(result, "message", "reason", "detail")),
        "transaction_id": _display(
            _first(result, "transaction_id", "transactionId", "command_id", "commandId")
        ),
        "observed_at": _display(
            _first(result, "observed_at", "observedAt", "recorded_at", "recordedAt")
        ),
    }


def _normalize_record(record: Any) -> dict[str, str]:
    item = _mapping(record)
    return {
        "record_id": _display(
            _first(
                item,
                "record_id",
                "audit_contract_id",
                "contract_id",
                "event_id",
                "id",
            )
        ),
        "sequence": _display(_first(item, "sequence", "ledger_offset", "offset")),
        "transaction_id": _display(
            _first(item, "transaction_id", "transactionId", "command_id")
        ),
        "counterparty": _display(
            _first(
                item,
                "counterparty",
                "counterparty_label",
                "counterparty_party_id",
                "counterpartyParty",
            )
        ),
        "amount": _display(_first(item, "amount", "value")),
        "recorded_at": _display(
            _first(
                item,
                "recorded_at",
                "recordedAt",
                "transactionTime",
                "timestamp",
                "created_at",
            )
        ),
        "raw": json.dumps(item, sort_keys=True, default=str),
    }


def _identity_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _first(value, "party_id", "partyId", "label", "name")
    return value


def _display_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    displayed: list[str] = []
    for item in value:
        if isinstance(item, dict):
            item = _first(item, "label", "name", "party_id", "partyId", "id")
        displayed.append(_display(item))
    return displayed


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def copy_records(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    return [dict(item) if isinstance(item, dict) else item for item in value]


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _display(value: Any) -> str:
    if value is None or value == "":
        return UNKNOWN
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, default=str)
    return str(value)


def _text(value: Any) -> str:
    return str(value) if value is not None else UNKNOWN


def _field(label: str, value: Any) -> str:
    return f"{label:<18} {safe_text(value)}"
