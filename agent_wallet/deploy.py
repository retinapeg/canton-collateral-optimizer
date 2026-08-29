"""Put the wallet on a real Canton node, and say plainly what is missing.

    python -m agent_wallet.deploy            # upload, allocate, grant, verify
    python -m agent_wallet.deploy --check    # diagnose only, change nothing

Against a local sandbox the DAR arrives via `--dar` and authentication is off,
so there is nothing to do.  Against a shared node (LocalNet, or the Cantor8
DevNet) three things have to happen first, and each has its own failure that is
easy to misread:

  1. the DAR must be uploaded          - otherwise PACKAGE_NAMES_NOT_FOUND
  2. the parties must exist            - otherwise NO_SYNCHRONIZER_ON_WHICH...
  3. the API user must be granted
     CanActAs on each of them          - otherwise 403, with a valid token

The third is the one that wastes an afternoon: a token says who you are, it
does not give you rights over a party.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from backend.canton import CantonClient, LedgerApiError

from . import network
from .ledger import ALL_PARTIES, PACKAGE_NAME, Wallet, party_hint

DAR = Path(__file__).parent / ".daml" / "dist" / "agent-wallet-0.0.1.dar"


def tick(ok: bool) -> str:
    return "ok  " if ok else "!!  "


class Deployment:
    def __init__(self, target: network.Target, *, dry_run: bool) -> None:
        self.target = target
        self.dry_run = dry_run
        self.user = network.client(target)
        self.admin = network.client(target, as_admin=True)
        self.problems: list[str] = []

    def fail(self, message: str) -> None:
        self.problems.append(message)
        print(f"  !!  {message}")

    # -- 0. can we reach it at all ------------------------------------------

    def reachable(self) -> bool:
        try:
            offset = self.user.ledger_end()
        except network.AuthError as exc:
            # We never got as far as the ledger: the identity provider refused
            # us. Say that, rather than blaming Canton.
            self.fail(str(exc))
            return False
        except LedgerApiError as exc:
            detail = str(exc)
            if "401" in detail:
                self.fail(
                    "401 unauthorized - the node wants a token this client did "
                    "not supply or could not mint.\n      "
                    f"Target was detected as {self.target.kind!r}; if that is "
                    "wrong set AGENT_WALLET_NETWORK, or set C8_IDP and "
                    "C8_CLIENT_SECRET for DevNet."
                )
            elif "403" in detail:
                self.fail(
                    "403 forbidden - the token is valid but this user has no "
                    "rights here. Ask for CanActAs, or use the admin user."
                )
            else:
                self.fail(f"cannot reach {self.target.base_url}: {detail}")
            return False
        print(f"  ok  reachable, ledger end at offset {offset}")
        return True

    # -- 1. the DAR ---------------------------------------------------------

    def package_present(self) -> bool:
        try:
            wallet = Wallet(self.user)
            wallet.ensure_parties(("Alice",))
        except LedgerApiError:
            pass  # party checks happen properly below; this is just a probe
        try:
            packages = self.user.get("/v2/packages") or {}
        except LedgerApiError as exc:
            self.fail(f"could not list packages: {exc}")
            return False
        return bool(packages.get("packageIds"))

    def upload_dar(self) -> None:
        if not DAR.exists():
            self.fail(f"{DAR} does not exist - run `daml build` in agent_wallet/ first")
            return
        if self.dry_run:
            print(f"  --  would upload {DAR.name} ({DAR.stat().st_size:,} bytes)")
            return
        try:
            # The packages endpoint takes the DAR bytes, not JSON, so it does
            # not go through the client's JSON helpers.
            from urllib.request import Request, urlopen

            request = Request(
                f"{self.target.base_url}/v2/packages",
                data=DAR.read_bytes(),
                headers={"Content-Type": "application/octet-stream"},
                method="POST",
            )
            provider = self.user.token_provider
            if provider is not None:
                request.add_header("Authorization", f"Bearer {provider()}")
            with urlopen(request, timeout=120) as response:
                if response.status not in (200, 201):
                    self.fail(f"DAR upload returned HTTP {response.status}")
                    return
        except Exception as exc:
            self.fail(
                f"DAR upload failed: {exc}\n      "
                "On some shared nodes only an administrator may upload "
                "packages; ask whoever runs the node to upload "
                f"{DAR.name} for you."
            )
            return
        print(f"  ok  uploaded {DAR.name} ({DAR.stat().st_size:,} bytes)")

    # -- 2 and 3. parties and rights ----------------------------------------

    def user_exists(self) -> bool:
        token = ""
        while True:
            query = "?pageSize=1000" + (f"&pageToken={token}" if token else "")
            page = self.admin.get(f"/v2/users{query}") or {}
            for user in page.get("users", []):
                if user.get("id") == self.target.user_id:
                    return True
            token = page.get("nextPageToken") or ""
            if not token:
                return False

    def ensure_user(self, rights: list[dict]) -> None:
        """Create the API user if the node has never heard of it.

        Easy to miss: allocating a party does not create a user, and granting
        rights to a user that does not exist is a 404 (`USER_NOT_FOUND`), not a
        helpful hint to create one first.
        """
        if self.user_exists():
            print(f"  ok  user {self.target.user_id!r} already exists")
            return
        if self.dry_run:
            print(f"  --  would create user {self.target.user_id!r}")
            return
        try:
            self.admin.post(
                "/v2/users",
                {
                    "user": {
                        "id": self.target.user_id,
                        "primaryParty": "",
                        "isDeactivated": False,
                        "identityProviderId": "",
                        "metadata": {"resourceVersion": "", "annotations": {}},
                    },
                    "rights": rights,
                },
            )
        except LedgerApiError as exc:
            self.fail(
                f"could not create user {self.target.user_id!r}: {exc}\n      "
                "On a node you do not administer, ask the operator to create "
                "this user and grant it CanActAs on your parties."
            )
            return
        print(f"  ok  created user {self.target.user_id!r}")

    def grant(self, party: str, hint: str) -> None:
        try:
            self.admin.post(
                f"/v2/users/{self.target.user_id}/rights",
                {
                    # This Canton wants the user and identity provider in the
                    # body as well as the path; omitting either is a bare 400.
                    "userId": self.target.user_id,
                    "identityProviderId": "",
                    "rights": [{"kind": {"CanActAs": {"value": {"party": party}}}}],
                },
            )
        except LedgerApiError as exc:
            if "already" not in str(exc).lower():
                self.fail(f"could not grant CanActAs on {hint}: {exc}")

    def parties_and_rights(self) -> dict[str, str]:
        parties: dict[str, str] = {}
        for hint in ALL_PARTIES:
            if self.dry_run:
                print(f"  --  would ensure party {hint}")
                continue
            try:
                parties[hint] = self.admin.ensure_party(hint)
            except LedgerApiError as exc:
                self.fail(
                    f"could not allocate {hint}: {exc}\n      "
                    "On DevNet party allocation may need the external-party "
                    "topology flow rather than POST /v2/parties. Ask the team "
                    "which applies, and give them your party ids."
                )
        rights = [
            {"kind": {"CanActAs": {"value": {"party": party}}}}
            for party in parties.values()
        ]
        self.ensure_user(rights)
        for hint, party in parties.items():
            self.grant(party, hint)
            print(f"  ok  {hint:<12} {party}")
        return parties

    # -- 4. prove it actually works -----------------------------------------

    def smoke(self, parties: dict[str, str]) -> None:
        if self.dry_run or not parties:
            return
        wallet = Wallet(self.user)
        try:
            wallet.active_contracts(parties["Alice"])
        except LedgerApiError as exc:
            self.fail(
                f"reading as Alice failed: {exc}\n      "
                "A 403 here means the CanActAs grant did not take effect for "
                f"user {self.target.user_id!r}."
            )
            return
        print("  ok  can read the ledger acting as Alice")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deploy the agent wallet to a Canton node"
    )
    parser.add_argument("--base-url", default=None, help="overrides C8_BASE")
    parser.add_argument(
        "--check",
        action="store_true",
        help="diagnose only; do not upload, allocate or grant anything",
    )
    args = parser.parse_args()

    try:
        target = network.from_env(args.base_url)
    except network.AuthError as exc:
        raise SystemExit(f"\nConfiguration problem:\n  {exc}\n")

    print(f"\nTarget: {target.describe()}")
    print(f"API user: {target.user_id}   admin user: {target.admin_user_id}\n")

    deployment = Deployment(target, dry_run=args.check)

    print("Reaching the node")
    if not deployment.reachable():
        raise SystemExit(_summary(deployment, target))

    print("\nPackage")
    if target.kind == network.SANDBOX:
        print("  --  sandbox: the DAR was supplied with --dar, nothing to upload")
    else:
        deployment.upload_dar()

    print("\nParties, user and rights")
    parties = deployment.parties_and_rights()

    print("\nVerifying")
    deployment.smoke(parties)

    raise SystemExit(_summary(deployment, target))


def _summary(deployment: Deployment, target: network.Target) -> int:
    print()
    if deployment.problems:
        print(f"{len(deployment.problems)} problem(s) above. Nothing is ready yet.")
        return 1
    if deployment.dry_run:
        print("Check only - nothing was changed. Re-run without --check to apply.")
        return 0
    port = "7575"
    print("Ready. Now run:")
    print(f"  python -m agent_wallet.demo  --base-url {target.base_url}")
    print(f"  python -m agent_wallet.serve --port {port} --base-url {target.base_url}")
    return 0


if __name__ == "__main__":
    main()
