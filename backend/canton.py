"""Small standard-library client for Canton's JSON Ledger API v2."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
from time import monotonic, sleep
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4


class LedgerApiError(RuntimeError):
    """Raised when Canton rejects or cannot receive a request."""


@dataclass(frozen=True)
class ActiveContract:
    contract_id: str
    template_id: str
    payload: dict[str, Any]
    witness_parties: tuple[str, ...]
    signatories: tuple[str, ...]
    observers: tuple[str, ...]

    @property
    def template_name(self) -> str:
        return self.template_id.rsplit(":", 1)[-1]


class CantonClient:
    def __init__(
        self,
        base_url: str = "http://localhost:7575",
        *,
        user_id: str = "collateral-demo-backend",
        timeout: float = 20.0,
        token_provider: Callable[[], str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.user_id = user_id
        self.timeout = timeout
        # A local sandbox runs with authentication disabled, so the default is
        # to send no credentials at all.  A shared node (LocalNet, DevNet)
        # answers 401 without a bearer token; pass a provider for those.  It is
        # called per request so a short-lived token can be refreshed.
        self.token_provider = token_provider

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.token_provider is not None:
            headers["Authorization"] = f"Bearer {self.token_provider()}"
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LedgerApiError(
                f"Canton returned HTTP {exc.code} for {method} {path}: {detail}"
            ) from exc
        except URLError as exc:
            raise LedgerApiError(
                f"Cannot reach Canton at {self.base_url}: {exc.reason}"
            ) from exc
        if not raw:
            return None
        return json.loads(raw)

    def get(self, path: str) -> Any:
        """Public seam for endpoints this class does not wrap.

        The JSON Ledger API differs slightly between Canton versions, so a
        caller pinned to a different SDK can reach its own endpoints without
        this client having to know about them.
        """
        return self._request("GET", path)

    def post(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        return self._request("POST", path, payload)

    def ledger_end(self) -> int:
        response = self._request("GET", "/v2/state/ledger-end")
        return int(response["offset"])

    def list_parties(self) -> list[str]:
        parties: list[str] = []
        page_token = ""
        while True:
            query = {"pageSize": 100}
            if page_token:
                query["pageToken"] = page_token
            response = self._request("GET", f"/v2/parties?{urlencode(query)}")
            parties.extend(row["party"] for row in response.get("partyDetails", []))
            page_token = response.get("nextPageToken") or ""
            if not page_token:
                return parties

    def ensure_party(self, hint: str) -> str:
        matches = [
            party for party in self.list_parties() if party.split("::", 1)[0] == hint
        ]
        if len(matches) > 1:
            raise LedgerApiError(
                f"More than one local party has the hint {hint!r}; use a fresh Sandbox"
            )
        if matches:
            return matches[0]
        deadline = monotonic() + self.timeout
        while True:
            try:
                response = self._request(
                    "POST", "/v2/parties", {"partyIdHint": hint}
                )
                return response["partyDetails"]["party"]
            except LedgerApiError as exc:
                # The HTTP service can become live just before Sandbox has
                # connected its participant to the synchronizer.  Retry only
                # this explicit, known-transient startup response.
                if (
                    "PARTY_ALLOCATION_WITHOUT_CONNECTED_SYNCHRONIZER" not in str(exc)
                    or monotonic() >= deadline
                ):
                    raise
                sleep(0.25)

    def active_contracts(self, party: str) -> list[ActiveContract]:
        contracts: list[ActiveContract] = []
        page_token: str | None = None
        while True:
            payload: dict[str, Any] = {
                "eventFormat": {
                    "filtersByParty": {party: {}},
                    "verbose": True,
                },
                "maxPageSize": 500,
            }
            if page_token:
                payload["pageToken"] = page_token
            response: Any = None
            try:
                response = self._request(
                    "POST", "/v2/state/active-contracts-page", payload
                )
                rows = response.get("activeContracts", [])
            except LedgerApiError as exc:
                # Canton 3.4 exposes the same ACS data at the non-paged route.
                # The wallet package is pinned to SDK 3.4.10, while the
                # collateral package uses 3.5.7. Supporting both shapes lets
                # one local sandbox host both DARs.
                if "HTTP 404" not in str(exc):
                    raise
                legacy_response = self._request(
                    "POST",
                    "/v2/state/active-contracts",
                    {
                        "filter": {"filtersByParty": {party: {}}},
                        "verbose": True,
                        "activeAtOffset": self.ledger_end(),
                    },
                )
                rows = legacy_response or []

            for row in rows:
                entry = row.get("contractEntry", {}).get("JsActiveContract")
                if not entry:
                    continue
                event = entry["createdEvent"]
                contracts.append(
                    ActiveContract(
                        contract_id=event["contractId"],
                        template_id=event["templateId"],
                        payload=event["createArgument"],
                        witness_parties=tuple(event.get("witnessParties", [])),
                        signatories=tuple(event.get("signatories", [])),
                        observers=tuple(event.get("observers", [])),
                    )
                )
            page_token = (
                response.get("nextPageToken")
                if isinstance(response, dict)
                else None
            )
            if not page_token:
                return contracts

    def submit(self, act_as: str, command: dict[str, Any], *, label: str) -> Any:
        return self.submit_multi(act_as=[act_as], command=command, label=label)

    def submit_multi(
        self,
        *,
        act_as: list[str],
        command: dict[str, Any],
        label: str,
    ) -> Any:
        """Submit acting as several parties at once.

        Needed for choices whose controllers are more than one party, such as a
        change that both signatories must agree to.
        """
        commands = {
            "commandId": f"{label}-{uuid4().hex}",
            "userId": self.user_id,
            "actAs": list(act_as),
            "commands": [command],
        }
        return self._request(
            "POST",
            "/v2/commands/submit-and-wait-for-transaction",
            {"commands": commands},
        )

    def create(
        self,
        *,
        act_as: str,
        template_id: str,
        arguments: dict[str, Any],
        label: str,
    ) -> Any:
        return self.submit(
            act_as,
            {
                "CreateCommand": {
                    "templateId": template_id,
                    "createArguments": arguments,
                }
            },
            label=label,
        )

    def exercise(
        self,
        *,
        act_as: str,
        template_id: str,
        contract_id: str,
        choice: str,
        choice_argument: dict[str, Any] | None = None,
        label: str,
    ) -> Any:
        return self.submit(
            act_as,
            {
                "ExerciseCommand": {
                    "templateId": template_id,
                    "contractId": contract_id,
                    "choice": choice,
                    "choiceArgument": choice_argument or {},
                }
            },
            label=label,
        )
