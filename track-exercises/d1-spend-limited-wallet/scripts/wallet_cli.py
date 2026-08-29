#!/usr/bin/env python3
"""Credential-safe, standard-library CLI for the spend-limited wallet demo.

The client deliberately contains no cap, expiry, allow-list, or revocation
policy checks.  It submits commands to Canton and reports the ledger's result.
"""

from __future__ import annotations

import argparse
import base64
import datetime as datetime_module
import decimal
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


SCHEMA_VERSION = 1
PACKAGE_NAME = "d1-spend-limited-wallet"
MODULE_NAME = "Mandate"
PROPOSAL_TEMPLATE = "MandateProposal"
MANDATE_TEMPLATE = "Mandate"
AUDIT_TEMPLATE = "ChargeRecord"
DEFAULT_BASE = "http://127.0.0.1:7575"
DEFAULT_AUDIENCE = "https://canton.network.global"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE_PATH = PROJECT_ROOT / ".wallet_cli_state.json"
ENV_STATUS_KEYS = (
    "C8_BASE",
    "C8_IDP",
    "C8_CLIENT_ID",
    "C8_CLIENT_SECRET",
    "C8_REGISTRY",
)
PARTY_HINTS = {
    "owner": "Owner",
    "agent": "Agent",
    "merchantA": "Merchant-A",
    "merchantB": "Merchant-B",
}


class WalletError(Exception):
    """A failure whose public fields are safe to serialize."""

    def __init__(self, category: str, message: str, **details: object) -> None:
        super().__init__(message)
        self.category = category
        self.message = message
        self.details = {key: value for key, value in details.items() if value is not None}

    def public(self) -> dict[str, object]:
        return {"category": self.category, "message": self.message, **self.details}


class LedgerError(WalletError):
    """A ledger rejection stripped of causes, contexts, headers, and IDs."""

    def __init__(self, details: dict[str, object]) -> None:
        if details.get("httpStatus") in {401, 403}:
            category = str(details.get("category") or "AUTHORIZATION")
        else:
            category = str(details.get("ledgerCode") or details.get("category") or "LEDGER_REJECTED")
        public_details = dict(details)
        public_details.pop("category", None)
        super().__init__(category, "The ledger rejected the request.", **public_details)


def _b64url(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def _safe_assertion(raw_text: str) -> str | None:
    """Return only known, non-sensitive Daml assertion labels."""
    lowered = raw_text.lower()
    known = (
        "charge would exceed the cap",
        "counterparty is not allow-listed",
        "amount must be positive",
        "mandate expired",
        "memo must not be empty",
        "proposal expired",
        "new cap below what is already spent",
        "new cap must be positive",
    )
    for assertion in known:
        if assertion in lowered:
            return assertion
    return None


def _sanitize_http_error(status: int, raw: bytes) -> dict[str, object]:
    parsed: object = None
    try:
        parsed = json.loads(raw.decode("utf-8", errors="replace")) if raw else {}
    except json.JSONDecodeError:
        parsed = {}
    body = parsed if isinstance(parsed, dict) else {}
    details: dict[str, object] = {
        "category": "AUTHENTICATION" if status == 401 else (
            "AUTHORIZATION" if status == 403 else "LEDGER_REJECTED"
        ),
        "httpStatus": status,
    }
    mapping = {
        "code": "ledgerCode",
        "errorCategory": "errorCategory",
        "grpcCodeValue": "grpcCodeValue",
        "definiteAnswer": "definiteAnswer",
    }
    for source, target in mapping.items():
        value = body.get(source)
        if isinstance(value, (str, int, float, bool)) or value is None:
            if value is not None:
                details[target] = value
    assertion = _safe_assertion(str(body.get("cause", "")))
    if assertion:
        details["assertion"] = assertion
    return details


def _utc_after(hours: float) -> str:
    value = datetime_module.datetime.now(datetime_module.timezone.utc)
    value += datetime_module.timedelta(hours=hours)
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _decimal_text(value: object) -> str:
    try:
        number = decimal.Decimal(str(value))
    except decimal.InvalidOperation as error:
        raise WalletError("INVALID_AMOUNT", "Amount must be a valid decimal string.") from error
    if not number.is_finite():
        raise WalletError("INVALID_AMOUNT", "Amount must be a finite decimal string.")
    return format(number, "f")


def _remaining(cap: object, spent: object) -> str | None:
    try:
        return format(decimal.Decimal(str(cap)) - decimal.Decimal(str(spent)), "f")
    except decimal.InvalidOperation:
        return None


def _walk(value: object):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _created_events(response: object) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    seen: set[str] = set()
    for node in _walk(response):
        candidate = node.get("CreatedEvent")
        if isinstance(candidate, dict):
            contract_id = candidate.get("contractId")
            if isinstance(contract_id, str) and contract_id not in seen:
                events.append(candidate)
                seen.add(contract_id)
    return events


def _update_id(response: dict[str, object]) -> str | None:
    transaction = response.get("transaction")
    if isinstance(transaction, dict) and isinstance(transaction.get("updateId"), str):
        return str(transaction["updateId"])
    return None


def _event_for_template(response: object, template_name: str) -> dict[str, object] | None:
    suffix = f":{MODULE_NAME}:{template_name}"
    for event in _created_events(response):
        if str(event.get("templateId", "")).endswith(suffix):
            return event
    return None


class LedgerClient:
    def __init__(self) -> None:
        self.idp = os.environ.get("C8_IDP", "").rstrip("/")
        self.mode = "DEVNET" if self.idp else "SANDBOX"
        self.base = os.environ.get("C8_BASE", DEFAULT_BASE).rstrip("/")
        self.client_id = os.environ.get("C8_CLIENT_ID", "hackathon")
        self.client_secret = os.environ.get("C8_CLIENT_SECRET")
        self.audience = os.environ.get("C8_AUD", DEFAULT_AUDIENCE)
        self.jwt_secret = os.environ.get("C8_JWT_SECRET", "unsafe").encode("utf-8")
        self.user = os.environ.get(
            "C8_USER", "ledger-api-user" if self.mode == "DEVNET" else "participant_admin"
        )
        self.admin_user = os.environ.get("C8_ADMIN_USER", "participant_admin")
        self._devnet_token: str | None = None

    def token(self, subject: str | None = None) -> str:
        if self.mode == "DEVNET":
            if not self.idp or not self.client_secret:
                raise WalletError(
                    "AUTH_CONFIGURATION",
                    "DevNet requires C8_IDP and C8_CLIENT_SECRET to be set.",
                )
            if self._devnet_token:
                return self._devnet_token
            encoded = urllib.parse.urlencode(
                {
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                }
            ).encode("utf-8")
            request = urllib.request.Request(
                f"{self.idp}/realms/master/protocol/openid-connect/token",
                data=encoded,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    raw_response = response.read()
                parsed = json.loads(raw_response)
            except urllib.error.HTTPError as error:
                error.read()
                raise WalletError(
                    "AUTHENTICATION",
                    "The identity provider rejected the client-credentials request.",
                    httpStatus=error.code,
                ) from None
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
                raise WalletError(
                    "AUTH_UNREACHABLE",
                    "Could not obtain a token from the configured identity provider.",
                ) from None
            token = parsed.get("access_token") if isinstance(parsed, dict) else None
            if not isinstance(token, str) or not token:
                raise WalletError("AUTH_RESPONSE", "The identity provider returned no access token.")
            self._devnet_token = token
            return token

        actual_subject = subject or self.user
        header = _b64url(json.dumps(
            {"alg": "HS256", "typ": "JWT"}, separators=(",", ":")
        ).encode("utf-8"))
        payload = _b64url(json.dumps(
            {"sub": actual_subject, "aud": self.audience}, separators=(",", ":")
        ).encode("utf-8"))
        signature = _b64url(hmac.new(
            self.jwt_secret, header + b"." + payload, hashlib.sha256
        ).digest())
        return (header + b"." + payload + b"." + signature).decode("ascii")

    def request(
        self,
        path: str,
        *,
        body: object | None = None,
        raw_body: bytes | None = None,
        method: str | None = None,
        subject: str | None = None,
    ) -> dict[str, object] | list[object]:
        if body is not None and raw_body is not None:
            raise WalletError("CLIENT_ERROR", "A request cannot contain two body encodings.")
        headers = {"Authorization": f"Bearer {self.token(subject)}"}
        data: bytes | None = None
        if raw_body is not None:
            data = raw_body
            headers["Content-Type"] = "application/octet-stream"
        elif body is not None:
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base + path,
            data=data,
            method=method or ("POST" if data is not None else "GET"),
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            safe = _sanitize_http_error(error.code, error.read())
            raise LedgerError(safe) from None
        except urllib.error.URLError:
            raise WalletError(
                "LEDGER_UNREACHABLE", "Could not reach the configured JSON Ledger API."
            ) from None
        except (TimeoutError, OSError):
            raise WalletError(
                "LEDGER_UNREACHABLE", "The configured JSON Ledger API did not respond."
            ) from None
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            raise WalletError("LEDGER_RESPONSE", "The ledger returned a non-JSON response.") from None
        if isinstance(parsed, (dict, list)):
            return parsed
        raise WalletError("LEDGER_RESPONSE", "The ledger returned an unexpected JSON value.")


class WalletCLI:
    def __init__(self, client: LedgerClient) -> None:
        self.client = client
        configured_state = os.environ.get("WALLET_CLI_STATE")
        self.state_path = Path(configured_state).expanduser() if configured_state else DEFAULT_STATE_PATH

    def _empty_state(self) -> dict[str, object]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "target": {
                "mode": self.client.mode,
                "fingerprint": hashlib.sha256(self.client.base.encode("utf-8")).hexdigest(),
            },
        }

    def load_state(self) -> dict[str, object]:
        if not self.state_path.exists():
            return self._empty_state()
        try:
            parsed = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise WalletError("STATE_INVALID", "The local wallet CLI state file is invalid.") from None
        if not isinstance(parsed, dict):
            raise WalletError("STATE_INVALID", "The local wallet CLI state file is invalid.")
        target = parsed.get("target")
        expected = self._empty_state()["target"]
        if target != expected:
            return self._empty_state()
        return parsed

    def save_state(self, state: dict[str, object]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_name(self.state_path.name + ".tmp")
        temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.state_path)

    def _manifest_identity(self) -> tuple[str, str]:
        manifest = PROJECT_ROOT / "daml.yaml"
        try:
            text = manifest.read_text(encoding="utf-8")
        except OSError:
            raise WalletError("MANIFEST_NOT_FOUND", "Could not read daml.yaml.") from None
        name_match = re.search(r"(?m)^name:\s*([^\s#]+)\s*$", text)
        version_match = re.search(r"(?m)^version:\s*([^\s#]+)\s*$", text)
        if not name_match or not version_match:
            raise WalletError(
                "MANIFEST_INVALID", "daml.yaml must contain name and version fields."
            )
        return name_match.group(1), version_match.group(1)

    def dar_path(self) -> Path:
        configured = os.environ.get("WALLET_DAR")
        if configured:
            path = Path(configured).expanduser().resolve()
            if not path.is_file():
                raise WalletError("DAR_NOT_FOUND", "WALLET_DAR does not identify a DAR file.")
            return path
        package_name, package_version = self._manifest_identity()
        expected = PROJECT_ROOT / ".daml" / "dist" / (
            f"{package_name}-{package_version}.dar"
        )
        if expected.is_file():
            return expected.resolve()
        raise WalletError(
            "DAR_NOT_FOUND",
            "Run `dpm build` first; the exact manifest-derived DAR is absent.",
        )

    def inspect_dar(self) -> dict[str, str]:
        dar = self.dar_path()
        executable = shutil.which("dpm")
        if not executable:
            fallback = Path.home() / ".dpm" / "bin" / "dpm"
            if fallback.is_file():
                executable = str(fallback)
        if not executable:
            raise WalletError("DPM_NOT_FOUND", "DPM is required to inspect the DAR.")
        environment = os.environ.copy()
        java_home = Path("/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home")
        java_bin = Path("/opt/homebrew/opt/openjdk@17/bin")
        if "JAVA_HOME" not in environment and java_home.exists():
            environment["JAVA_HOME"] = str(java_home)
        if java_bin.exists():
            environment["PATH"] = str(java_bin) + os.pathsep + environment.get("PATH", "")
        completed = subprocess.run(
            [executable, "damlc", "inspect-dar", str(dar), "--json"],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise WalletError("DAR_INSPECTION_FAILED", "DPM could not inspect the DAR.")
        try:
            inspected = json.loads(completed.stdout)
            package_id = inspected["main_package_id"]
            package = inspected["packages"][package_id]
            package_name = package["name"]
            package_version = package["version"]
        except (json.JSONDecodeError, KeyError, TypeError):
            raise WalletError("DAR_INSPECTION_FAILED", "DAR metadata was incomplete.") from None
        if not all(isinstance(value, str) and value for value in (
            package_id, package_name, package_version
        )):
            raise WalletError("DAR_INSPECTION_FAILED", "DAR metadata was incomplete.")
        manifest_name, manifest_version = self._manifest_identity()
        if package_name != manifest_name or package_version != manifest_version:
            raise WalletError(
                "DAR_METADATA_MISMATCH",
                "The inspected main package name/version does not match daml.yaml.",
                manifestPackageName=manifest_name,
                manifestPackageVersion=manifest_version,
                inspectedPackageName=package_name,
                inspectedPackageVersion=package_version,
            )
        try:
            display_path = str(dar.relative_to(PROJECT_ROOT))
        except ValueError:
            # WALLET_DAR may deliberately point at an artifact built in another
            # isolated worktree. Keep its real path for the local read, but do
            # not serialize that machine-specific absolute path.
            display_path = f"<external>/{dar.name}"
        return {
            "path": display_path,
            "absolutePath": str(dar),
            "packageId": package_id,
            "packageName": package_name,
            "packageVersion": package_version,
        }

    def _list_package_ids(self) -> list[str]:
        response = self.client.request("/v2/packages", subject=self.client.admin_user)
        values = response.get("packageIds", []) if isinstance(response, dict) else []
        return [value for value in values if isinstance(value, str)]

    def _package_status(self, package_id: str) -> str | None:
        encoded = urllib.parse.quote(package_id, safe="")
        response = self.client.request(
            f"/v2/packages/{encoded}/status", subject=self.client.admin_user
        )
        value = response.get("packageStatus") if isinstance(response, dict) else None
        return value if isinstance(value, str) else None

    def _connected_synchronizer_ids(self) -> set[str]:
        """Return connected upload targets without serializing their identifiers."""
        response = self.client.request(
            "/v2/state/connected-synchronizers",
            subject=self.client.admin_user,
        )
        if not isinstance(response, dict):
            raise WalletError(
                "SYNCHRONIZER_RESPONSE",
                "The participant returned invalid connected-synchronizer data.",
            )
        connected = response.get("connectedSynchronizers", [])
        if not isinstance(connected, list):
            raise WalletError(
                "SYNCHRONIZER_RESPONSE",
                "The participant returned invalid connected-synchronizer data.",
            )
        return {
            str(item["synchronizerId"])
            for item in connected
            if (
                isinstance(item, dict)
                and isinstance(item.get("synchronizerId"), str)
                and str(item["synchronizerId"]).strip()
            )
        }

    def _version_collisions(
        self, metadata: dict[str, str]
    ) -> tuple[list[dict[str, str]], dict[str, object]]:
        page_token = ""
        matches: list[dict[str, str]] = []
        for _ in range(100):
            body: dict[str, object] = {
                "packageMetadataFilter": {
                    "packageNamePrefixes": [metadata["packageName"]]
                },
                "pageSize": 100,
            }
            if page_token:
                body["pageToken"] = page_token
            try:
                response = self.client.request(
                    "/v2/package-vetting/list",
                    body=body,
                    subject=self.client.admin_user,
                )
            except LedgerError as error:
                if error.details.get("httpStatus") == 401:
                    raise
                return [], {"mode": "participant", "reason": error.category}
            if not isinstance(response, dict):
                return [], {"mode": "participant", "reason": "unexpected_metadata_response"}
            for group in response.get("vettedPackages", []):
                if not isinstance(group, dict):
                    continue
                for package in group.get("packages", []):
                    if not isinstance(package, dict):
                        continue
                    name = package.get("packageName")
                    version = package.get("packageVersion")
                    package_id = package.get("packageId")
                    if name == metadata["packageName"] and version == metadata["packageVersion"]:
                        if isinstance(package_id, str) and package_id != metadata["packageId"]:
                            matches.append({
                                "packageId": package_id,
                                "packageName": str(name),
                                "packageVersion": str(version),
                            })
            next_token = response.get("nextPageToken")
            if not isinstance(next_token, str) or not next_token:
                return matches, {"mode": "metadata", "checked": True}
            page_token = next_token
        raise WalletError("PACKAGE_METADATA_PAGINATION", "Package metadata pagination did not terminate.")

    def upload_dar(self) -> dict[str, object]:
        metadata = self.inspect_dar()
        package_id = metadata["packageId"]
        package_ids = self._list_package_ids()
        if package_id in package_ids:
            status = self._package_status(package_id)
            if status != "PACKAGE_STATUS_REGISTERED":
                raise WalletError(
                    "PACKAGE_NOT_REGISTERED",
                    "The participant knows the package ID but cannot execute it.",
                    packageStatus=status,
                )
            result: dict[str, object] = {
                "uploadResult": "already_present",
                "packageStatus": status,
                **metadata,
            }
        else:
            # Local state is only evidence about this participant if its saved
            # package ID is still listed there. This avoids a false collision
            # after a fresh sandbox starts on the same URL.
            saved_package = self.load_state().get("package")
            if (
                isinstance(saved_package, dict)
                and saved_package.get("packageName") == metadata["packageName"]
                and saved_package.get("packageVersion") == metadata["packageVersion"]
                and isinstance(saved_package.get("packageId"), str)
                and saved_package["packageId"] != package_id
                and saved_package["packageId"] in package_ids
            ):
                raise WalletError(
                    "PACKAGE_VERSION_COLLISION",
                    "A different package already uses this package name and version; bump the Daml package patch version and rebuild.",
                    packageName=metadata["packageName"],
                    packageVersion=metadata["packageVersion"],
                    existingPackageIds=[saved_package["packageId"]],
                )
            collisions, guard = self._version_collisions(metadata)
            if collisions:
                raise WalletError(
                    "PACKAGE_VERSION_COLLISION",
                    "A different package already uses this package name and version; bump the Daml package patch version and rebuild.",
                    packageName=metadata["packageName"],
                    packageVersion=metadata["packageVersion"],
                    existingPackageIds=[item["packageId"] for item in collisions],
                )
            configured_synchronizer = os.environ.get("C8_SYNCHRONIZER_ID", "").strip()
            selected_synchronizer = configured_synchronizer
            selection_source = "configured"
            if not selected_synchronizer:
                connected_synchronizers: set[str] = set()
                for attempt in range(20):
                    connected_synchronizers = self._connected_synchronizer_ids()
                    if connected_synchronizers:
                        break
                    if attempt < 19:
                        time.sleep(0.25)
                if len(connected_synchronizers) != 1:
                    raise WalletError(
                        "SYNCHRONIZER_SELECTION_REQUIRED",
                        "Package upload requires exactly one connected synchronizer or an explicit C8_SYNCHRONIZER_ID.",
                        connectedSynchronizerCount=len(connected_synchronizers),
                    )
                selected_synchronizer = next(iter(connected_synchronizers))
                selection_source = "connected_synchronizers"
            upload_path = "/v2/packages?" + urllib.parse.urlencode({
                "synchronizerId": selected_synchronizer,
                "vetAllPackages": "true",
            })
            dar_bytes = Path(metadata["absolutePath"]).read_bytes()
            try:
                self.client.request(
                    upload_path,
                    raw_body=dar_bytes,
                    method="POST",
                    subject=self.client.admin_user,
                )
            except LedgerError as error:
                if error.details.get("ledgerCode") in {
                    "KNOWN_PACKAGE_VERSION", "KNOWN_DAR_VERSION"
                }:
                    raise WalletError(
                        "PACKAGE_VERSION_COLLISION",
                        "The participant rejected different code with this package name/version; bump the Daml package patch version and rebuild.",
                        **error.details,
                    ) from None
                raise
            if package_id not in self._list_package_ids():
                raise WalletError("PACKAGE_UPLOAD_UNVERIFIED", "The uploaded package ID was not listed.")
            status = self._package_status(package_id)
            if status != "PACKAGE_STATUS_REGISTERED":
                raise WalletError(
                    "PACKAGE_UPLOAD_UNVERIFIED",
                    "The uploaded package is not registered for execution.",
                    packageStatus=status,
                )
            result = {
                "uploadResult": "uploaded",
                "packageStatus": status,
                "collisionGuard": guard,
                "synchronizerSelection": selection_source,
                **metadata,
            }
        result.pop("absolutePath", None)
        state = self.load_state()
        state["package"] = {
            "packageId": metadata["packageId"],
            "packageName": metadata["packageName"],
            "packageVersion": metadata["packageVersion"],
            "darPath": metadata["path"],
        }
        self.save_state(state)
        return result

    def _all_parties(self) -> list[dict[str, object]]:
        parties: list[dict[str, object]] = []
        page_token = ""
        for _ in range(100):
            query = {"pageSize": "1000"}
            if page_token:
                query["pageToken"] = page_token
            path = "/v2/parties?" + urllib.parse.urlencode(query)
            response = self.client.request(path, subject=self.client.admin_user)
            if not isinstance(response, dict):
                raise WalletError("PARTY_RESPONSE", "The participant returned invalid party data.")
            for party in response.get("partyDetails", []):
                if isinstance(party, dict):
                    parties.append(party)
            next_token = response.get("nextPageToken")
            if not isinstance(next_token, str) or not next_token:
                return parties
            page_token = next_token
        raise WalletError("PARTY_PAGINATION", "Party pagination did not terminate.")

    def local_parties(self) -> list[dict[str, object]]:
        return [
            party for party in self._all_parties()
            if party.get("isLocal") is True and isinstance(party.get("party"), str)
        ]

    @staticmethod
    def _hint(party: str) -> str:
        return party.split("::", 1)[0]

    def list_parties(self) -> dict[str, object]:
        local = sorted(
            (
                {"party": str(item["party"]), "hint": self._hint(str(item["party"])), "isLocal": True}
                for item in self.local_parties()
            ),
            key=lambda item: str(item["party"]),
        )
        return {"count": len(local), "parties": local}

    def _find_local_party(self, value: str, *, required: bool = True) -> str | None:
        local = [str(item["party"]) for item in self.local_parties()]
        exact = sorted(party for party in local if party == value)
        hinted = sorted(party for party in local if self._hint(party) == value)
        if exact:
            return exact[0]
        if len(hinted) == 1:
            return hinted[0]
        if len(hinted) > 1:
            raise WalletError(
                "AMBIGUOUS_LOCAL_PARTY",
                "Multiple local parties share this hint; provide one exact full party ID.",
                hint=value,
                matchCount=len(hinted),
            )
        if required:
            raise WalletError("LOCAL_PARTY_NOT_FOUND", "No local party matches the requested value.")
        return None

    def _allocate_or_reuse(self, hint: str) -> tuple[str, str]:
        existing = self._find_local_party(hint, required=False)
        if existing:
            return existing, "reused"
        response = self.client.request(
            "/v2/parties",
            body={"partyIdHint": hint},
            subject=self.client.admin_user,
        )
        details = response.get("partyDetails") if isinstance(response, dict) else None
        party = details.get("party") if isinstance(details, dict) else None
        if not isinstance(party, str):
            raise WalletError("PARTY_ALLOCATION", "The participant returned no allocated party.")
        verified = self._find_local_party(party, required=False)
        if verified != party:
            raise WalletError("NON_LOCAL_PARTY", "The allocated party is not local to this participant.")
        return party, "allocated"

    def _rights(self) -> list[dict[str, object]]:
        encoded_user = urllib.parse.quote(self.client.user, safe="")
        response = self.client.request(
            f"/v2/users/{encoded_user}/rights", subject=self.client.admin_user
        )
        rights = response.get("rights", []) if isinstance(response, dict) else []
        return [right for right in rights if isinstance(right, dict)]

    def _can_act_as(self, party: str, rights: list[dict[str, object]] | None = None) -> bool:
        for right in rights if rights is not None else self._rights():
            try:
                if right["kind"]["CanActAs"]["value"]["party"] == party:  # type: ignore[index]
                    return True
            except (KeyError, TypeError):
                continue
        return False

    def _grant_act_as(self, party: str) -> None:
        encoded_user = urllib.parse.quote(self.client.user, safe="")
        self.client.request(
            f"/v2/users/{encoded_user}/rights",
            body={
                "userId": self.client.user,
                "identityProviderId": "",
                "rights": [{"kind": {"CanActAs": {"value": {"party": party}}}}],
            },
            subject=self.client.admin_user,
        )

    def _require_can_act_as(self, party: str) -> None:
        if not self._find_local_party(party, required=False):
            raise WalletError("NON_LOCAL_PARTY", "Refusing to submit as a non-local party.")
        if not self._can_act_as(party):
            raise WalletError(
                "CAN_ACT_AS_MISSING",
                "The configured ledger user lacks CanActAs for a submitting party.",
                party=party,
                user=self.client.user,
            )

    def setup_demo(self) -> dict[str, object]:
        parties: dict[str, str] = {}
        actions: dict[str, str] = {}
        for role, hint in PARTY_HINTS.items():
            party, action = self._allocate_or_reuse(hint)
            parties[role] = party
            actions[role] = action
        rights = self._rights()
        for party in parties.values():
            if not self._can_act_as(party, rights):
                self._grant_act_as(party)
        verified_rights = self._rights()
        verification = {
            role: self._can_act_as(party, verified_rights) for role, party in parties.items()
        }
        if not all(verification.values()):
            raise WalletError(
                "CAN_ACT_AS_MISSING",
                "CanActAs could not be verified for every local demo party.",
                verification=verification,
            )
        state = self.load_state()
        state["parties"] = parties
        self.save_state(state)
        return {"parties": parties, "partyActions": actions, "canActAs": verification}

    def _party_state(self) -> dict[str, str]:
        state = self.load_state()
        saved = state.get("parties")
        if isinstance(saved, dict) and all(isinstance(saved.get(role), str) for role in PARTY_HINTS):
            parties = {role: str(saved[role]) for role in PARTY_HINTS}
            for party in parties.values():
                if not self._find_local_party(party, required=False):
                    break
            else:
                return parties
        parties: dict[str, str] = {}
        for role, hint in PARTY_HINTS.items():
            found = self._find_local_party(hint, required=False)
            if not found:
                raise WalletError("DEMO_NOT_SETUP", "Run setup-demo before this command.")
            parties[role] = found
        state["parties"] = parties
        self.save_state(state)
        return parties

    def _package_metadata(self) -> dict[str, str]:
        state = self.load_state()
        package = state.get("package")
        if isinstance(package, dict) and isinstance(package.get("packageId"), str):
            return {key: str(value) for key, value in package.items() if isinstance(value, str)}
        inspected = self.inspect_dar()
        return {
            "packageId": inspected["packageId"],
            "packageName": inspected["packageName"],
            "packageVersion": inspected["packageVersion"],
            "darPath": inspected["path"],
        }

    def _template_id(self, template: str) -> str:
        return f"{self._package_metadata()['packageId']}:{MODULE_NAME}:{template}"

    def _template_filter_id(self, template: str) -> str:
        return f"#{self._package_metadata().get('packageName', PACKAGE_NAME)}:{MODULE_NAME}:{template}"

    def _submit(self, commands: list[dict[str, object]], act_as: str | list[str]) -> dict[str, object]:
        submitters = act_as if isinstance(act_as, list) else [act_as]
        for party in submitters:
            self._require_can_act_as(party)
        inner = {
            "commands": commands,
            "commandId": f"wallet-cli-{uuid.uuid4()}",
            "actAs": submitters,
            "userId": self.client.user,
            "packageIdSelectionPreference": [self._package_metadata()["packageId"]],
        }
        response = self.client.request(
            "/v2/commands/submit-and-wait-for-transaction",
            body={"commands": inner},
            subject=self.client.user,
        )
        if not isinstance(response, dict):
            raise WalletError("TRANSACTION_RESPONSE", "The ledger returned no transaction object.")
        if not _update_id(response):
            raise WalletError("TRANSACTION_RESPONSE", "The transaction response has no update ID.")
        return response

    def _active_contracts(self, party: str, template: str) -> list[dict[str, object]]:
        ledger_end = self.client.request("/v2/state/ledger-end", subject=self.client.user)
        offset = ledger_end.get("offset") if isinstance(ledger_end, dict) else None
        if offset is None:
            raise WalletError("LEDGER_RESPONSE", "The ledger end response has no offset.")
        body = {
            "filter": {
                "filtersByParty": {
                    party: {
                        "cumulative": [{
                            "identifierFilter": {
                                "TemplateFilter": {
                                    "value": {
                                        "templateId": self._template_filter_id(template),
                                        "includeCreatedEventBlob": False,
                                    }
                                }
                            }
                        }]
                    }
                }
            },
            "verbose": True,
            "activeAtOffset": offset,
        }
        response = self.client.request(
            "/v2/state/active-contracts", body=body, subject=self.client.user
        )
        if not isinstance(response, list):
            raise WalletError("CONTRACT_RESPONSE", "The active-contract response is invalid.")
        contracts: list[dict[str, object]] = []
        for item in response:
            if not isinstance(item, dict):
                continue
            entry = item.get("contractEntry")
            active = entry.get("JsActiveContract") if isinstance(entry, dict) else None
            event = active.get("createdEvent") if isinstance(active, dict) else None
            if isinstance(event, dict):
                contracts.append(event)
        contracts.sort(key=lambda event: str(event.get("createdAt", "")))
        return contracts

    @staticmethod
    def _payload(event: dict[str, object]) -> dict[str, object]:
        value = event.get("createArgument")
        return value if isinstance(value, dict) else {}

    def create_mandate(self) -> dict[str, object]:
        upload = self.upload_dar()
        setup = self.setup_demo()
        parties = setup["parties"]
        assert isinstance(parties, dict)
        run_id = uuid.uuid4().hex
        reference = f"wallet-demo-{run_id[:16]}"
        expires_at = _utc_after(float(os.environ.get("WALLET_EXPIRY_HOURS", "24")))
        proposal_arguments = {
            "owner": parties["owner"],
            "agent": parties["agent"],
            "cap": "100.0",
            "expiresAt": expires_at,
            "allowedCounterparties": [parties["merchantA"]],
            "mandateReference": reference,
        }
        proposal_response = self._submit(
            [{"CreateCommand": {
                "templateId": self._template_id(PROPOSAL_TEMPLATE),
                "createArguments": proposal_arguments,
            }}],
            str(parties["owner"]),
        )
        proposal_event = _event_for_template(proposal_response, PROPOSAL_TEMPLATE)
        proposal_cid = proposal_event.get("contractId") if proposal_event else None
        if not isinstance(proposal_cid, str):
            candidates = [
                event for event in self._active_contracts(str(parties["owner"]), PROPOSAL_TEMPLATE)
                if self._payload(event).get("mandateReference") == reference
            ]
            proposal_cid = candidates[-1].get("contractId") if candidates else None
        if not isinstance(proposal_cid, str):
            raise WalletError("PROPOSAL_NOT_FOUND", "The committed proposal could not be queried.")
        accept_response = self._submit(
            [{"ExerciseCommand": {
                "templateId": self._template_id(PROPOSAL_TEMPLATE),
                "contractId": proposal_cid,
                "choice": "Accept",
                "choiceArgument": {},
            }}],
            str(parties["agent"]),
        )
        mandate_event = _event_for_template(accept_response, MANDATE_TEMPLATE)
        mandate_cid = mandate_event.get("contractId") if mandate_event else None
        if not isinstance(mandate_cid, str):
            candidates = [
                event for event in self._active_contracts(str(parties["owner"]), MANDATE_TEMPLATE)
                if self._payload(event).get("mandateReference") == reference
            ]
            mandate_cid = candidates[-1].get("contractId") if candidates else None
        if not isinstance(mandate_cid, str):
            raise WalletError("MANDATE_NOT_FOUND", "The accepted mandate could not be queried.")
        state = self.load_state()
        state["workflow"] = {
            "runId": run_id,
            "mandateReference": reference,
            "proposalCid": proposal_cid,
            "mandateCid": mandate_cid,
            "expiresAt": expires_at,
            "revoked": False,
        }
        self.save_state(state)
        return {
            "packageUpload": upload["uploadResult"],
            "parties": parties,
            "mandateReference": reference,
            "proposalContractId": proposal_cid,
            "mandateContractId": mandate_cid,
            "cap": "100.0",
            "allowedCounterparties": [parties["merchantA"]],
            "expiresAt": expires_at,
            "proposalUpdateId": _update_id(proposal_response),
            "acceptUpdateId": _update_id(accept_response),
        }

    def _workflow(self) -> dict[str, object]:
        value = self.load_state().get("workflow")
        return value if isinstance(value, dict) else {}

    def _current_mandate_event(self) -> dict[str, object] | None:
        parties = self._party_state()
        workflow = self._workflow()
        reference = workflow.get("mandateReference")
        if not isinstance(reference, str) or not reference:
            return None
        events = [
            event for event in self._active_contracts(parties["owner"], MANDATE_TEMPLATE)
            if self._payload(event).get("mandateReference") == reference
        ]
        if not events:
            return None
        preferred = workflow.get("mandateCid")
        for event in events:
            if event.get("contractId") == preferred:
                return event
        return events[-1]

    def status(self) -> dict[str, object]:
        parties = self._party_state()
        workflow = self._workflow()
        mandate = self._current_mandate_event()
        reference = workflow.get("mandateReference")
        if isinstance(reference, str):
            proposals = [
                event for event in self._active_contracts(parties["owner"], PROPOSAL_TEMPLATE)
                if self._payload(event).get("mandateReference") == reference
            ]
        else:
            proposals = []
        mandate_view: dict[str, object] | None = None
        if mandate:
            payload = self._payload(mandate)
            mandate_view = {
                "contractId": mandate.get("contractId"),
                **payload,
                "remainingAllowance": _remaining(payload.get("cap"), payload.get("spent")),
            }
            state = self.load_state()
            saved_workflow = state.get("workflow")
            if isinstance(saved_workflow, dict) and isinstance(mandate.get("contractId"), str):
                saved_workflow["mandateCid"] = mandate["contractId"]
                state["workflow"] = saved_workflow
                self.save_state(state)
        return {
            "parties": parties,
            "mandateReference": reference,
            "activeMandate": mandate_view,
            "activeProposalCount": len(proposals),
            "revoked": bool(workflow.get("revoked")) and mandate is None,
        }

    def _mandate_cid(self, *, allow_stale: bool = False) -> str:
        workflow = self._workflow()
        reference = workflow.get("mandateReference")
        if not isinstance(reference, str) or not reference:
            raise WalletError(
                "MANDATE_CONTEXT_MISSING",
                "No saved mandate workflow exists; run create-mandate before mutating a mandate.",
            )
        saved = workflow.get("mandateCid")
        event = self._current_mandate_event()
        contract_id = event.get("contractId") if event else None
        if isinstance(contract_id, str):
            return contract_id
        if allow_stale and isinstance(saved, str):
            return saved
        raise WalletError("MANDATE_NOT_FOUND", "No mandate contract is available.")

    def charge(self, counterparty: str, amount: str, memo: str) -> dict[str, object]:
        parties = self._party_state()
        resolved_counterparty = self._find_local_party(counterparty)
        assert resolved_counterparty is not None
        amount_text = _decimal_text(amount)
        mandate_cid = self._mandate_cid(allow_stale=True)
        response = self._submit(
            [{"ExerciseCommand": {
                "templateId": self._template_id(MANDATE_TEMPLATE),
                "contractId": mandate_cid,
                "choice": "Charge",
                "choiceArgument": {
                    "counterparty": resolved_counterparty,
                    "amount": amount_text,
                    "memo": memo,
                },
            }}],
            parties["agent"],
        )
        mandate_event = _event_for_template(response, MANDATE_TEMPLATE)
        audit_event = _event_for_template(response, AUDIT_TEMPLATE)
        new_mandate_cid = mandate_event.get("contractId") if mandate_event else None
        audit_cid = audit_event.get("contractId") if audit_event else None
        if not isinstance(new_mandate_cid, str):
            active = self._current_mandate_event()
            new_mandate_cid = active.get("contractId") if active else None
        if not isinstance(new_mandate_cid, str) or not isinstance(audit_cid, str):
            raise WalletError(
                "CHARGE_RESULT_INCOMPLETE",
                "The charge committed but its replacement mandate or audit record was not visible.",
                updateId=_update_id(response),
            )
        state = self.load_state()
        workflow = state.get("workflow")
        if not isinstance(workflow, dict):
            workflow = {}
        workflow["mandateCid"] = new_mandate_cid
        workflow["lastAuditCid"] = audit_cid
        workflow["lastChargeUpdateId"] = _update_id(response)
        state["workflow"] = workflow
        self.save_state(state)
        return {
            "updateId": _update_id(response),
            "previousMandateContractId": mandate_cid,
            "mandateContractId": new_mandate_cid,
            "auditContractId": audit_cid,
            "counterparty": resolved_counterparty,
            "amount": amount_text,
            "memo": memo,
        }

    def revoke(self) -> dict[str, object]:
        parties = self._party_state()
        mandate_cid = self._mandate_cid()
        response = self._submit(
            [{"ExerciseCommand": {
                "templateId": self._template_id(MANDATE_TEMPLATE),
                "contractId": mandate_cid,
                "choice": "Revoke",
                "choiceArgument": {},
            }}],
            parties["owner"],
        )
        state = self.load_state()
        workflow = state.get("workflow")
        if not isinstance(workflow, dict):
            workflow = {}
        workflow["mandateCid"] = mandate_cid
        workflow["revoked"] = True
        workflow["revokeUpdateId"] = _update_id(response)
        state["workflow"] = workflow
        self.save_state(state)
        return {"updateId": _update_id(response), "revokedMandateContractId": mandate_cid}

    def audit(self) -> dict[str, object]:
        parties = self._party_state()
        workflow = self._workflow()
        reference = workflow.get("mandateReference")
        if not isinstance(reference, str) or not reference:
            return {"mandateReference": None, "count": 0, "records": []}
        records: list[dict[str, object]] = []
        for event in self._active_contracts(parties["owner"], AUDIT_TEMPLATE):
            payload = self._payload(event)
            if payload.get("mandateReference") != reference:
                continue
            records.append({"contractId": event.get("contractId"), **payload})
        records.sort(key=lambda item: str(item.get("transactionTime", "")))
        return {"mandateReference": reference, "count": len(records), "records": records}

    def health(self) -> dict[str, object]:
        self.client.token(self.client.user)
        ledger_end = self.client.request("/v2/state/ledger-end", subject=self.client.user)
        local = self.local_parties()
        rights = self._rights()
        local_ids = [str(item["party"]) for item in local]
        can_act_as = sorted(party for party in local_ids if self._can_act_as(party, rights))
        return {
            "environment": {
                key: "SET" if os.environ.get(key) else "UNSET" for key in ENV_STATUS_KEYS
            },
            "checks": {
                "authentication": "ok",
                "ledgerEnd": ledger_end.get("offset") if isinstance(ledger_end, dict) else None,
                "localPartyCount": len(local_ids),
                "canActAsLocalPartyCount": len(can_act_as),
            },
        }

    @staticmethod
    def _rejection(
        label: str,
        action,
        *,
        expected_assertion: str | None = None,
        expected_ledger_codes: set[str] | None = None,
    ) -> dict[str, object]:
        try:
            action()
        except LedgerError as error:
            safe = dict(error.details)
            http_status = safe.get("httpStatus")
            ledger_code = safe.get("ledgerCode")
            if http_status in {401, 403} or ledger_code in {
                "UNAUTHENTICATED", "PERMISSION_DENIED"
            }:
                raise WalletError(
                    "ATTACK_INFRASTRUCTURE_FAILURE",
                    "The attack reached an authentication or authorization failure, not the Daml rule under test.",
                    attack=label,
                    httpStatus=http_status,
                    ledgerCode=ledger_code,
                ) from None
            assertion_matches = (
                expected_assertion is None or safe.get("assertion") == expected_assertion
            )
            code_matches = (
                expected_ledger_codes is None or ledger_code in expected_ledger_codes
            )
            if not assertion_matches or not code_matches:
                raise WalletError(
                    "ATTACK_REJECTION_MISMATCH",
                    "The ledger rejected the command for a reason other than the scenario's required Daml rule.",
                    attack=label,
                    httpStatus=http_status,
                    ledgerCode=ledger_code,
                    assertion=safe.get("assertion"),
                ) from None
            return {
                "attack": label,
                "rejected": True,
                "rawCategory": {
                    key: safe[key] for key in (
                        "httpStatus", "ledgerCode", "errorCategory", "grpcCodeValue",
                        "definiteAnswer", "assertion"
                    ) if key in safe
                },
            }
        raise WalletError(
            "ATTACK_UNEXPECTEDLY_SUCCEEDED",
            "A deliberately invalid command was accepted by the ledger.",
            attack=label,
        )

    def run_demo(self) -> dict[str, object]:
        package = self.upload_dar()
        setup = self.setup_demo()
        created = self.create_mandate()
        parties = created["parties"]
        assert isinstance(parties, dict)
        reference = str(created["mandateReference"])
        successful = self.charge(
            str(parties["merchantA"]), "30", f"{reference}:merchant-a-30"
        )
        after_success = self.status()
        active = after_success.get("activeMandate")
        if not isinstance(active, dict):
            raise WalletError("DEMO_ASSERTION", "No replacement mandate exists after the valid charge.")
        if decimal.Decimal(str(active.get("spent"))) != decimal.Decimal("30"):
            raise WalletError("DEMO_ASSERTION", "The replacement mandate does not show spent = 30.")
        if decimal.Decimal(str(active.get("remainingAllowance"))) != decimal.Decimal("70"):
            raise WalletError("DEMO_ASSERTION", "The replacement mandate does not show 70 remaining.")
        audit_after_success = self.audit()
        if audit_after_success.get("count") != 1:
            raise WalletError("DEMO_ASSERTION", "The valid charge did not produce exactly one audit record.")
        rejections = [
            self._rejection(
                "cap_exceeded",
                lambda: self.charge(
                    str(parties["merchantA"]), "80", f"{reference}:merchant-a-80"
                ),
                expected_assertion="charge would exceed the cap",
                expected_ledger_codes={"DAML_FAILURE"},
            ),
            self._rejection(
                "counterparty_not_allowed",
                lambda: self.charge(
                    str(parties["merchantB"]), "10", f"{reference}:merchant-b-10"
                ),
                expected_assertion="counterparty is not allow-listed",
                expected_ledger_codes={"DAML_FAILURE"},
            ),
        ]
        revoked = self.revoke()
        rejections.append(self._rejection(
            "revoked_mandate",
            lambda: self.charge(
                str(parties["merchantA"]), "1", f"{reference}:merchant-a-after-revoke"
            ),
            expected_ledger_codes={
                "CONTRACT_NOT_FOUND",
                "CONTRACT_NOT_ACTIVE",
                "CONTRACT_NOT_FOUND_OR_NOT_ACTIVE",
            },
        ))
        final_audit = self.audit()
        records = final_audit.get("records")
        if final_audit.get("count") != 1 or not isinstance(records, list) or len(records) != 1:
            raise WalletError("DEMO_ASSERTION", "Final audit does not contain exactly one charge.")
        if decimal.Decimal(str(records[0].get("amount"))) != decimal.Decimal("30"):
            raise WalletError("DEMO_ASSERTION", "Final audit's only charge is not 30.")
        if records[0].get("counterparty") != parties["merchantA"]:
            raise WalletError(
                "DEMO_ASSERTION", "Final audit's only charge is not for Merchant-A."
            )
        if records[0].get("mandateReference") != reference:
            raise WalletError(
                "DEMO_ASSERTION", "Final audit's only charge is for a different mandate."
            )
        return {
            "packageUpload": package,
            "parties": setup["parties"],
            "mandate": created,
            "successfulCharge": successful,
            "statusAfterCharge": after_success,
            "auditAfterCharge": audit_after_success,
            "deliberateRejections": rejections,
            "revocation": revoked,
            "finalAudit": final_audit,
            "proof": {
                "successfulChargeCount": 1,
                "successfulAmount": "30",
                "remainingBeforeRevocation": "70",
                "failedChargesCreatedAuditRecords": False,
            },
        }


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise WalletError("USAGE_ERROR", message)


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(description="Spend-limited wallet JSON Ledger API CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in (
        "health", "upload-dar", "list-parties", "setup-demo", "create-mandate",
        "status", "revoke", "audit", "run-demo",
    ):
        subparsers.add_parser(command)
    charge = subparsers.add_parser("charge")
    charge.add_argument("--counterparty", required=True)
    charge.add_argument("--amount", required=True)
    charge.add_argument("--memo", required=True)
    return parser


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    command = arguments[0] if arguments else "unknown"
    client = LedgerClient()
    try:
        parsed = build_parser().parse_args(arguments)
        command = str(parsed.command)
        cli = WalletCLI(client)
        if command == "health":
            result = cli.health()
        elif command == "upload-dar":
            result = cli.upload_dar()
        elif command == "list-parties":
            result = cli.list_parties()
        elif command == "setup-demo":
            result = cli.setup_demo()
        elif command == "create-mandate":
            result = cli.create_mandate()
        elif command == "status":
            result = cli.status()
        elif command == "charge":
            result = cli.charge(parsed.counterparty, parsed.amount, parsed.memo)
        elif command == "revoke":
            result = cli.revoke()
        elif command == "audit":
            result = cli.audit()
        elif command == "run-demo":
            result = cli.run_demo()
        else:
            raise WalletError("USAGE_ERROR", "Unknown command.")
        _emit({
            "schemaVersion": SCHEMA_VERSION,
            "ok": True,
            "command": command,
            "mode": client.mode,
            "result": result,
        })
        return 0
    except WalletError as error:
        _emit({
            "schemaVersion": SCHEMA_VERSION,
            "ok": False,
            "command": command,
            "mode": client.mode,
            "error": error.public(),
        })
        return 1
    except (KeyboardInterrupt, BrokenPipeError):
        _emit({
            "schemaVersion": SCHEMA_VERSION,
            "ok": False,
            "command": command,
            "mode": client.mode,
            "error": {"category": "INTERRUPTED", "message": "The command was interrupted."},
        })
        return 1
    except Exception:  # Last-resort envelope; never serialize exception details.
        _emit({
            "schemaVersion": SCHEMA_VERSION,
            "ok": False,
            "command": command,
            "mode": client.mode,
            "error": {
                "category": "INTERNAL_ERROR",
                "message": "The CLI encountered an unexpected internal error.",
            },
        })
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
