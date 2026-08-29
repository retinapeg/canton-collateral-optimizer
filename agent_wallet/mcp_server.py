"""An MCP server that hands a language model a spend-limited wallet.

    python -m agent_wallet.mcp_server

Speaks JSON-RPC 2.0 over stdio, newline-delimited.  Standard library only, so
there is nothing to install.

The important property: **this server never checks a limit.**  `pay` builds a
command and submits it, every time, whatever the model asks for.  The cap, the
allow-list, the expiry and the revocation all live in the Daml choice body, and
what comes back on a refusal is the ledger's own message.

That is what makes it safe to let a model drive.  You cannot talk a ledger into
anything.  There is no prompt, no system message and no amount of insistence
that reaches the rule, because the rule is not in the model's context and is not
in this file -- it is in a contract the model has no authority over.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

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

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "agent-wallet", "version": "0.0.1"}

DEFAULT_CAP = Decimal(os.environ.get("AGENT_WALLET_CAP", "100"))
DEFAULT_PERIOD_LIMIT = Decimal(os.environ.get("AGENT_WALLET_PERIOD_LIMIT", "40"))
DEFAULT_BALANCE = Decimal(os.environ.get("AGENT_WALLET_BALANCE", "10000"))
BASE_URL = os.environ.get("AGENT_WALLET_BASE_URL", "http://localhost:7575")
REFERENCE = os.environ.get("AGENT_WALLET_REFERENCE", "agent-wallet-mcp")


TOOLS = [
    {
        "name": "wallet_status",
        "description": (
            "What this wallet is allowed to do right now: the total cap and how "
            "much of it is left, the per-period limit, who may be paid, when the "
            "mandate expires, and whether the owner has revoked it. Read this "
            "before spending."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_payees",
        "description": (
            "The counterparties this wallet may pay. Anyone not on this list will "
            "be refused by the ledger, whatever the reason for paying them."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "pay",
        "description": (
            "Pay a counterparty from the wallet. The payment is submitted to the "
            "Canton ledger, which enforces the spending limits; this tool does "
            "not check them and cannot override them. If the ledger refuses, the "
            "reply is the ledger's own reason and no money has moved."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "payee": {
                    "type": "string",
                    "description": "Counterparty name, e.g. CoffeeShop",
                },
                "amount": {
                    "type": "string",
                    "description": "Amount to pay, e.g. 4.50",
                },
                "memo": {
                    "type": "string",
                    "description": "What the payment is for",
                },
            },
            "required": ["payee", "amount"],
        },
    },
    {
        "name": "statement",
        "description": (
            "Every charge this wallet has made, with the permission that allowed "
            "each one, as recorded on the ledger."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]


class WalletSession:
    """Lazily attaches to a mandate on the ledger, creating one if needed."""

    def __init__(self) -> None:
        self.client, self.target = network.client_from_env(BASE_URL)
        self.wallet = Wallet(self.client)
        self.parties: dict[str, str] = {}
        self.mandate: Mandate | None = None
        self.authority: str | None = None

    def connect(self) -> Mandate:
        if self.mandate is not None:
            refreshed = self.wallet.read_mandate(self.parties[AGENT], self.mandate.reference)
            if refreshed is not None:
                self.mandate = refreshed
            return self.mandate

        self.client.ledger_end()
        self.parties = self.wallet.ensure_parties()
        agent = self.parties[AGENT]

        for mandate in self.wallet.read_mandates(agent):
            if mandate.reference == REFERENCE and self.wallet.authority_is_live(
                agent, mandate.authority_cid
            ):
                self.mandate = mandate
                self.authority = mandate.authority_cid
                return mandate

        self.mandate = self._provision()
        self.authority = self.mandate.authority_cid
        return self.mandate

    def _provision(self) -> Mandate:
        owner = self.parties[OWNER]
        agent = self.parties[AGENT]
        account = self.wallet.open_account(
            bank=self.parties[BANK],
            owner=owner,
            balance=DEFAULT_BALANCE,
            viewers=[agent],
        )
        authority = self.wallet.issue_authority(
            owner=owner, agent=agent, label="mcp-session"
        )
        proposal = self.wallet.propose_mandate(
            owner=owner,
            agent=agent,
            account_cid=account,
            authority_cid=authority,
            reference=REFERENCE,
            cap=DEFAULT_CAP,
            period_limit=DEFAULT_PERIOD_LIMIT,
            period_length=timedelta(days=1),
            allowed_payees=[self.parties[p] for p in ALLOWED_PAYEES],
            expires_in=timedelta(days=1),
        )
        return self.wallet.accept_mandate(agent=agent, proposal_cid=proposal)

    def resolve_payee(self, name: str) -> str | None:
        wanted = name.strip().lower()
        for hint, party in self.parties.items():
            if hint.lower() == wanted:
                return party
        for party in self.parties.values():
            if party_hint(party).lower() == wanted:
                return party
        return None


# -- the tools ----------------------------------------------------------------


def tool_wallet_status(session: WalletSession, _: dict[str, Any]) -> str:
    m = session.connect()
    live = session.wallet.authority_is_live(session.parties[OWNER], m.authority_cid)
    balance = session.wallet.read_balance(session.parties[OWNER], m.account_cid)
    lines = [
        f"Mandate '{m.reference}' from {party_hint(m.owner)} "
        f"to {party_hint(m.agent)}: {'LIVE' if live else 'REVOKED BY THE OWNER'}",
        f"  Total cap        {money(m.cap)}",
        f"  Spent so far     {money(m.spent)}",
        f"  Remaining        {money(m.remaining)}",
    ]
    if m.period_limit is not None:
        lines += [
            f"  Per-period limit {money(m.period_limit)} per "
            f"{_describe(m.period_length)}",
            f"  Spent this period {money(m.spent_in_period)} "
            f"(leaves {money(m.period_remaining)})",
        ]
    lines += [
        f"  May pay          {', '.join(party_hint(p) for p in m.allowed_payees)}",
        f"  Expires          {m.expires_at:%Y-%m-%d %H:%M:%S} UTC",
    ]
    if balance is not None:
        lines.append(
            f"  Funding account holds {money(balance)} -- but this mandate can "
            f"only reach {money(m.remaining)} of it."
        )
    if not live:
        lines.append(
            "  The owner has revoked this wallet. Nothing further can be spent."
        )
    return "\n".join(lines)


def tool_list_payees(session: WalletSession, _: dict[str, Any]) -> str:
    m = session.connect()
    allowed = [party_hint(p) for p in m.allowed_payees]
    known = sorted(
        hint
        for hint in session.parties
        if hint not in (BANK, OWNER, AGENT) and hint not in allowed
    )
    out = ["May be paid: " + ", ".join(allowed)]
    if known:
        out.append(
            "Known to the ledger but NOT payable from this wallet: " + ", ".join(known)
        )
    return "\n".join(out)


def tool_pay(session: WalletSession, args: dict[str, Any]) -> str:
    m = session.connect()
    name = str(args.get("payee", "")).strip()
    raw = str(args.get("amount", "")).strip().replace(",", "").lstrip("$£€")
    memo = str(args.get("memo", "")).strip() or "no memo"

    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        raise ToolError(f"{raw!r} is not an amount I can send to the ledger.")

    payee = session.resolve_payee(name)
    if payee is None:
        raise ToolError(
            f"No party named {name!r} exists on this ledger, so there is nothing "
            "to submit. This is a name lookup failing, not a spending limit. "
            "Use list_payees to see who exists."
        )

    # No limit check here, deliberately. Submit it and let the ledger rule.
    updated = session.wallet.charge(
        agent=session.parties[AGENT],
        mandate_cid=m.contract_id,
        payee=payee,
        amount=amount,
        memo=memo,
    )
    session.mandate = updated
    return (
        f"Paid {money(amount)} to {party_hint(payee)} for {memo}.\n"
        f"Remaining on the mandate: {money(updated.remaining)} of {money(updated.cap)}."
    )


def tool_statement(session: WalletSession, _: dict[str, Any]) -> str:
    m = session.connect()
    owner = session.parties[OWNER]
    receipts = session.wallet.read_receipts(owner, m.reference)
    if not receipts:
        return "No charges yet on this mandate."
    out = [f"Statement for mandate '{m.reference}':"]
    for r in receipts:
        out.append(
            f"  {r.charged_at:%Y-%m-%d %H:%M:%S}  {party_hint(r.payee):<12} "
            f"{money(r.amount):>10}  {r.memo}"
        )
        out.append(
            f"      allowed because: "
            f"{statement_mod._shorten_parties(r.justification)}"
        )
    total = sum((r.amount for r in receipts), Decimal("0"))
    out.append(f"  Total {money(total)}; {money(m.remaining)} still available.")
    return "\n".join(out)


HANDLERS = {
    "wallet_status": tool_wallet_status,
    "list_payees": tool_list_payees,
    "pay": tool_pay,
    "statement": tool_statement,
}


def _describe(delta: timedelta) -> str:
    if delta == timedelta(days=1):
        return "day"
    if delta == timedelta(days=7):
        return "week"
    if delta >= timedelta(days=1):
        return f"{delta.days} days"
    return f"{int(delta.total_seconds() // 3600)} hours"


class ToolError(RuntimeError):
    """Something we could not even submit. Not a ledger refusal."""


# -- JSON-RPC -----------------------------------------------------------------


def handle(request: dict[str, Any], session: WalletSession) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")

    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        )
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name")
        handler = HANDLERS.get(name)
        if handler is None:
            return _result(
                request_id, _content(f"No such tool: {name}", is_error=True)
            )
        try:
            return _result(
                request_id, _content(handler(session, params.get("arguments") or {}))
            )
        except Refused as exc:
            # The ledger said no. Give the model the ledger's words, verbatim,
            # so it can see that this was not our decision to make.
            return _result(
                request_id,
                _content(
                    "The ledger refused this payment. No money moved.\n\n"
                    "  Reason from the ledger: "
                    f"{statement_mod._shorten_parties(exc.reason)}\n\n"
                    "This limit is enforced in the Daml contract, not in this "
                    "tool. It cannot be raised by asking, and this tool has no "
                    "way to bypass it. Only the wallet's owner can change the "
                    "mandate.",
                    is_error=True,
                ),
            )
        except ToolError as exc:
            return _result(request_id, _content(str(exc), is_error=True))
        except LedgerApiError as exc:
            return _result(
                request_id,
                _content(f"Could not reach the ledger: {exc}", is_error=True),
            )

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _content(text: str, *, is_error: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        payload["isError"] = True
    return payload


def main() -> None:
    session = WalletSession()
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            continue
        response = handle(request, session)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
