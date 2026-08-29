#!/usr/bin/env python3
"""Fail-closed unit tests for wallet_cli's sanitized rejection matching."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import wallet_cli


def reject(details: dict[str, object]):
    def action() -> None:
        raise wallet_cli.LedgerError(details)

    return action


class RejectionMatchingTests(unittest.TestCase):
    def test_cap_assertion_is_accepted(self) -> None:
        result = wallet_cli.WalletCLI._rejection(
            "cap_exceeded",
            reject({
                "httpStatus": 400,
                "ledgerCode": "DAML_FAILURE",
                "errorCategory": 9,
                "grpcCodeValue": 9,
                "assertion": "charge would exceed the cap",
            }),
            expected_assertion="charge would exceed the cap",
            expected_ledger_codes={"DAML_FAILURE"},
        )
        self.assertTrue(result["rejected"])

    def test_allow_list_assertion_is_accepted(self) -> None:
        result = wallet_cli.WalletCLI._rejection(
            "counterparty_not_allowed",
            reject({
                "httpStatus": 400,
                "ledgerCode": "DAML_FAILURE",
                "assertion": "counterparty is not allow-listed",
            }),
            expected_assertion="counterparty is not allow-listed",
            expected_ledger_codes={"DAML_FAILURE"},
        )
        self.assertTrue(result["rejected"])

    def test_stale_contract_code_is_accepted(self) -> None:
        result = wallet_cli.WalletCLI._rejection(
            "revoked_mandate",
            reject({"httpStatus": 404, "ledgerCode": "CONTRACT_NOT_FOUND"}),
            expected_ledger_codes={"CONTRACT_NOT_FOUND"},
        )
        self.assertTrue(result["rejected"])

    def test_401_fails_closed(self) -> None:
        with self.assertRaisesRegex(wallet_cli.WalletError, "authentication or authorization") as raised:
            wallet_cli.WalletCLI._rejection(
                "cap_exceeded",
                reject({"httpStatus": 401, "ledgerCode": "UNAUTHENTICATED"}),
                expected_assertion="charge would exceed the cap",
            )
        self.assertEqual(raised.exception.category, "ATTACK_INFRASTRUCTURE_FAILURE")

    def test_403_fails_closed(self) -> None:
        with self.assertRaises(wallet_cli.WalletError) as raised:
            wallet_cli.WalletCLI._rejection(
                "counterparty_not_allowed",
                reject({"httpStatus": 403, "ledgerCode": "PERMISSION_DENIED"}),
                expected_assertion="counterparty is not allow-listed",
            )
        self.assertEqual(raised.exception.category, "ATTACK_INFRASTRUCTURE_FAILURE")

    def test_unrelated_daml_assertion_fails_closed(self) -> None:
        with self.assertRaises(wallet_cli.WalletError) as raised:
            wallet_cli.WalletCLI._rejection(
                "cap_exceeded",
                reject({
                    "httpStatus": 400,
                    "ledgerCode": "DAML_FAILURE",
                    "assertion": "amount must be positive",
                }),
                expected_assertion="charge would exceed the cap",
                expected_ledger_codes={"DAML_FAILURE"},
            )
        self.assertEqual(raised.exception.category, "ATTACK_REJECTION_MISMATCH")

    def test_malformed_command_error_fails_closed(self) -> None:
        with self.assertRaises(wallet_cli.WalletError) as raised:
            wallet_cli.WalletCLI._rejection(
                "counterparty_not_allowed",
                reject({"httpStatus": 400, "ledgerCode": "INVALID_ARGUMENT"}),
                expected_assertion="counterparty is not allow-listed",
                expected_ledger_codes={"DAML_FAILURE"},
            )
        self.assertEqual(raised.exception.category, "ATTACK_REJECTION_MISMATCH")

    def test_right_assertion_with_wrong_code_fails_closed(self) -> None:
        with self.assertRaises(wallet_cli.WalletError) as raised:
            wallet_cli.WalletCLI._rejection(
                "cap_exceeded",
                reject({
                    "httpStatus": 400,
                    "ledgerCode": "INVALID_ARGUMENT",
                    "assertion": "charge would exceed the cap",
                }),
                expected_assertion="charge would exceed the cap",
                expected_ledger_codes={"DAML_FAILURE"},
            )
        self.assertEqual(raised.exception.category, "ATTACK_REJECTION_MISMATCH")

    def test_unrelated_post_revoke_error_fails_closed(self) -> None:
        with self.assertRaises(wallet_cli.WalletError) as raised:
            wallet_cli.WalletCLI._rejection(
                "revoked_mandate",
                reject({"httpStatus": 400, "ledgerCode": "DAML_FAILURE"}),
                expected_ledger_codes={"CONTRACT_NOT_FOUND"},
            )
        self.assertEqual(raised.exception.category, "ATTACK_REJECTION_MISMATCH")


class SanitizationTests(unittest.TestCase):
    def test_error_sanitization_whitelists_only_known_fields(self) -> None:
        raw = (
            b'{"code":"DAML_FAILURE","cause":"charge would exceed the cap",'
            b'"context":{"secret":"do-not-emit"},"errorCategory":9,'
            b'"grpcCodeValue":9,"definiteAnswer":false}'
        )
        sanitized = wallet_cli._sanitize_http_error(400, raw)
        self.assertEqual(sanitized["assertion"], "charge would exceed the cap")
        self.assertNotIn("cause", sanitized)
        self.assertNotIn("context", sanitized)
        self.assertNotIn("secret", str(sanitized))

    def test_401_and_403_keep_auth_categories(self) -> None:
        unauthorized = wallet_cli.LedgerError(
            wallet_cli._sanitize_http_error(401, b'{"code":"UNAUTHENTICATED"}')
        )
        forbidden = wallet_cli.LedgerError(
            wallet_cli._sanitize_http_error(403, b'{"code":"PERMISSION_DENIED"}')
        )
        self.assertEqual(unauthorized.category, "AUTHENTICATION")
        self.assertEqual(forbidden.category, "AUTHORIZATION")
        self.assertEqual(unauthorized.details["ledgerCode"], "UNAUTHENTICATED")
        self.assertEqual(forbidden.details["ledgerCode"], "PERMISSION_DENIED")


class OneReadResponse:
    def __init__(self, payload: object) -> None:
        self.raw = json.dumps(payload).encode("utf-8")
        self.read_count = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        return False

    def read(self) -> bytes:
        self.read_count += 1
        if self.read_count == 1:
            return self.raw
        return b""


class DevNetTokenTests(unittest.TestCase):
    def test_token_body_is_read_once_cached_and_absent_from_health_json(self) -> None:
        access_token = "unit-test-access-token-never-serialize"
        client_secret = "unit-test-client-secret-never-serialize"
        idp_response = OneReadResponse({"access_token": access_token})
        responses = [
            idp_response,
            OneReadResponse({"offset": 7}),
            OneReadResponse({"partyDetails": [], "nextPageToken": ""}),
            OneReadResponse({"rights": []}),
        ]
        environment = {
            "C8_BASE": "https://ledger.invalid",
            "C8_IDP": "https://idp.invalid",
            "C8_CLIENT_ID": "test-client",
            "C8_CLIENT_SECRET": client_secret,
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            with mock.patch.object(
                wallet_cli.urllib.request, "urlopen", side_effect=responses
            ) as urlopen:
                client = wallet_cli.LedgerClient()
                result = wallet_cli.WalletCLI(client).health()
                self.assertEqual(client.token(), access_token)
        rendered = json.dumps(result, sort_keys=True)
        self.assertEqual(idp_response.read_count, 1)
        self.assertEqual(urlopen.call_count, 4)
        self.assertNotIn(access_token, rendered)
        self.assertNotIn(client_secret, rendered)


class PackageUploadTests(unittest.TestCase):
    @staticmethod
    def client() -> mock.Mock:
        client = mock.Mock()
        client.mode = "SANDBOX"
        client.base = "http://127.0.0.1:7575"
        client.admin_user = "participant_admin"
        return client

    def test_connected_synchronizer_endpoint_works_without_package_metadata(self) -> None:
        client = self.client()
        client.request.return_value = {
            "connectedSynchronizers": [
                {"synchronizerAlias": "fresh", "synchronizerId": "sync::one"},
                {"synchronizerAlias": "blank", "synchronizerId": ""},
            ]
        }
        cli = wallet_cli.WalletCLI(client)
        self.assertEqual(cli._connected_synchronizer_ids(), {"sync::one"})
        client.request.assert_called_once_with(
            "/v2/state/connected-synchronizers",
            subject="participant_admin",
        )

    def test_malformed_connected_synchronizer_response_fails_closed(self) -> None:
        for response in ([], {"connectedSynchronizers": "not-a-list"}):
            with self.subTest(response_type=type(response).__name__):
                client = self.client()
                client.request.return_value = response
                cli = wallet_cli.WalletCLI(client)
                with self.assertRaises(wallet_cli.WalletError) as raised:
                    cli._connected_synchronizer_ids()
                self.assertEqual(raised.exception.category, "SYNCHRONIZER_RESPONSE")

    def test_vetting_metadata_checks_collisions_without_selecting_a_target(self) -> None:
        client = self.client()
        client.request.side_effect = [
            {
                "vettedPackages": [{
                    "synchronizerId": "sync::one",
                    "packages": [{
                        "packageName": "d1-spend-limited-wallet",
                        "packageVersion": "0.0.1",
                        "packageId": "old-package-id",
                    }],
                }],
                "nextPageToken": "page-two",
            },
            {
                "vettedPackages": [{
                    "synchronizerId": "sync::one",
                    "packages": [],
                }],
                "nextPageToken": "",
            },
        ]
        cli = wallet_cli.WalletCLI(client)
        matches, guard = cli._version_collisions({
            "packageId": "new-package-id",
            "packageName": "d1-spend-limited-wallet",
            "packageVersion": "0.0.1",
        })
        self.assertEqual([item["packageId"] for item in matches], ["old-package-id"])
        self.assertEqual(guard, {"mode": "metadata", "checked": True})
        self.assertEqual(client.request.call_count, 2)

    def test_upload_uses_participant_derived_synchronizer_without_serializing_it(self) -> None:
        client = self.client()
        with tempfile.TemporaryDirectory() as directory:
            dar = Path(directory) / "business.dar"
            dar.write_bytes(b"test-dar")
            cli = wallet_cli.WalletCLI(client)
            cli.state_path = Path(directory) / "state.json"
            metadata = {
                "path": "<external>/business.dar",
                "absolutePath": str(dar),
                "packageId": "new-package-id",
                "packageName": "d1-spend-limited-wallet",
                "packageVersion": "0.0.1",
            }
            with mock.patch.object(cli, "inspect_dar", return_value=metadata), \
                 mock.patch.object(
                     cli, "_list_package_ids", side_effect=[[], ["new-package-id"]]
                 ), \
                 mock.patch.object(
                     cli,
                     "_version_collisions",
                     return_value=([], {"mode": "metadata", "checked": True}),
                 ), \
                 mock.patch.object(
                     cli, "_connected_synchronizer_ids", return_value={"sync::one"}
                 ), \
                 mock.patch.object(
                     cli, "_package_status", return_value="PACKAGE_STATUS_REGISTERED"
                 ):
                result = cli.upload_dar()
        request_path = client.request.call_args.args[0]
        self.assertEqual(
            request_path,
            "/v2/packages?synchronizerId=sync%3A%3Aone&vetAllPackages=true",
        )
        self.assertEqual(result["synchronizerSelection"], "connected_synchronizers")
        self.assertNotIn("sync::one", json.dumps(result))
        self.assertNotIn("absolutePath", result)

    def test_absent_or_ambiguous_synchronizer_fails_before_post(self) -> None:
        for connected in (set(), {"sync::one", "sync::two"}):
            with self.subTest(connected_count=len(connected)):
                client = self.client()
                with tempfile.TemporaryDirectory() as directory:
                    dar = Path(directory) / "business.dar"
                    dar.write_bytes(b"test-dar")
                    cli = wallet_cli.WalletCLI(client)
                    cli.state_path = Path(directory) / "state.json"
                    metadata = {
                        "path": "business.dar",
                        "absolutePath": str(dar),
                        "packageId": "new-package-id",
                        "packageName": "d1-spend-limited-wallet",
                        "packageVersion": "0.0.1",
                    }
                    with mock.patch.dict(os.environ, {}, clear=True), \
                         mock.patch.object(cli, "inspect_dar", return_value=metadata), \
                         mock.patch.object(cli, "_list_package_ids", return_value=[]), \
                         mock.patch.object(
                             cli,
                             "_version_collisions",
                             return_value=([], {"mode": "metadata", "checked": True}),
                         ), \
                         mock.patch.object(
                             cli, "_connected_synchronizer_ids", return_value=connected
                         ), \
                         mock.patch.object(wallet_cli.time, "sleep"), \
                         mock.patch.object(Path, "read_bytes") as read_bytes:
                        with self.assertRaises(wallet_cli.WalletError) as raised:
                            cli.upload_dar()
                self.assertEqual(
                    raised.exception.category, "SYNCHRONIZER_SELECTION_REQUIRED"
                )
                self.assertEqual(
                    raised.exception.details["connectedSynchronizerCount"], len(connected)
                )
                read_bytes.assert_not_called()
                client.request.assert_not_called()

    def test_explicit_synchronizer_override_qualifies_upload(self) -> None:
        client = self.client()
        with tempfile.TemporaryDirectory() as directory:
            dar = Path(directory) / "business.dar"
            dar.write_bytes(b"test-dar")
            cli = wallet_cli.WalletCLI(client)
            cli.state_path = Path(directory) / "state.json"
            metadata = {
                "path": "business.dar",
                "absolutePath": str(dar),
                "packageId": "new-package-id",
                "packageName": "d1-spend-limited-wallet",
                "packageVersion": "0.0.1",
            }
            with mock.patch.dict(
                     os.environ, {"C8_SYNCHRONIZER_ID": "sync::configured"}, clear=True
                 ), \
                 mock.patch.object(cli, "inspect_dar", return_value=metadata), \
                 mock.patch.object(
                     cli, "_list_package_ids", side_effect=[[], ["new-package-id"]]
                 ), \
                 mock.patch.object(
                     cli,
                     "_version_collisions",
                     return_value=([], {"mode": "metadata", "checked": True}),
                 ), \
                 mock.patch.object(cli, "_connected_synchronizer_ids") as discover, \
                 mock.patch.object(
                     cli, "_package_status", return_value="PACKAGE_STATUS_REGISTERED"
                 ):
                result = cli.upload_dar()
        discover.assert_not_called()
        self.assertEqual(
            client.request.call_args.args[0],
            "/v2/packages?synchronizerId=sync%3A%3Aconfigured&vetAllPackages=true",
        )
        self.assertEqual(result["synchronizerSelection"], "configured")
        self.assertNotIn("sync::configured", json.dumps(result))

    def test_listed_saved_package_collision_fails_before_upload(self) -> None:
        client = self.client()
        with tempfile.TemporaryDirectory() as directory:
            cli = wallet_cli.WalletCLI(client)
            cli.state_path = Path(directory) / "state.json"
            state = cli._empty_state()
            state["package"] = {
                "packageId": "old-package-id",
                "packageName": "d1-spend-limited-wallet",
                "packageVersion": "0.0.1",
            }
            cli.save_state(state)
            metadata = {
                "path": "business.dar",
                "absolutePath": str(Path(directory) / "business.dar"),
                "packageId": "new-package-id",
                "packageName": "d1-spend-limited-wallet",
                "packageVersion": "0.0.1",
            }
            with mock.patch.object(cli, "inspect_dar", return_value=metadata), \
                 mock.patch.object(cli, "_list_package_ids", return_value=["old-package-id"]), \
                 mock.patch.object(cli, "_version_collisions") as vetting:
                with self.assertRaises(wallet_cli.WalletError) as raised:
                    cli.upload_dar()
        self.assertEqual(raised.exception.category, "PACKAGE_VERSION_COLLISION")
        vetting.assert_not_called()
        client.request.assert_not_called()


class DarIdentityTests(unittest.TestCase):
    @staticmethod
    def client() -> mock.Mock:
        return mock.Mock(mode="SANDBOX", base="http://127.0.0.1:7575")

    def test_dar_path_rejects_a_stale_sole_dist_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "daml.yaml").write_text(
                "sdk-version: 3.5.7\nname: wallet-current\nversion: 2.0.0\n",
                encoding="utf-8",
            )
            dist = root / ".daml" / "dist"
            dist.mkdir(parents=True)
            (dist / "wallet-stale-1.0.0.dar").write_bytes(b"stale")
            cli = wallet_cli.WalletCLI(self.client())
            with mock.patch.object(wallet_cli, "PROJECT_ROOT", root), \
                 mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(wallet_cli.WalletError) as raised:
                    cli.dar_path()
        self.assertEqual(raised.exception.category, "DAR_NOT_FOUND")

    def test_inspected_package_identity_must_match_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "daml.yaml").write_text(
                "sdk-version: 3.5.7\nname: wallet-current\nversion: 2.0.0\n",
                encoding="utf-8",
            )
            dist = root / ".daml" / "dist"
            dist.mkdir(parents=True)
            dar = dist / "wallet-current-2.0.0.dar"
            dar.write_bytes(b"dar")
            inspected = {
                "main_package_id": "package-id",
                "packages": {
                    "package-id": {"name": "wallet-stale", "version": "1.0.0"}
                },
            }
            completed = mock.Mock(returncode=0, stdout=json.dumps(inspected))
            cli = wallet_cli.WalletCLI(self.client())
            with mock.patch.object(wallet_cli, "PROJECT_ROOT", root), \
                 mock.patch.dict(os.environ, {}, clear=True), \
                 mock.patch.object(wallet_cli.shutil, "which", return_value="/fake/dpm"), \
                 mock.patch.object(wallet_cli.subprocess, "run", return_value=completed):
                with self.assertRaises(wallet_cli.WalletError) as raised:
                    cli.inspect_dar()
        self.assertEqual(raised.exception.category, "DAR_METADATA_MISMATCH")


class PartyResolutionTests(unittest.TestCase):
    def test_duplicate_local_hints_require_an_exact_party_id(self) -> None:
        client = mock.Mock(mode="SANDBOX", base="http://127.0.0.1:7575")
        cli = wallet_cli.WalletCLI(client)
        parties = [
            {"party": "Owner::namespace-one", "isLocal": True},
            {"party": "Owner::namespace-two", "isLocal": True},
        ]
        with mock.patch.object(cli, "local_parties", return_value=parties):
            with self.assertRaises(wallet_cli.WalletError) as raised:
                cli._find_local_party("Owner")
            exact = cli._find_local_party("Owner::namespace-two")
        self.assertEqual(raised.exception.category, "AMBIGUOUS_LOCAL_PARTY")
        self.assertEqual(raised.exception.details["matchCount"], 2)
        self.assertEqual(exact, "Owner::namespace-two")


class WorkflowIsolationTests(unittest.TestCase):
    def test_missing_reference_never_selects_an_unrelated_mandate(self) -> None:
        client = mock.Mock(mode="SANDBOX", base="http://127.0.0.1:7575")
        cli = wallet_cli.WalletCLI(client)
        with mock.patch.object(cli, "_party_state", return_value={"owner": "Owner::local"}), \
             mock.patch.object(cli, "_workflow", return_value={}), \
             mock.patch.object(cli, "_active_contracts") as active:
            self.assertIsNone(cli._current_mandate_event())
        active.assert_not_called()

    def test_missing_reference_blocks_mutation_before_ledger_submission(self) -> None:
        client = mock.Mock(mode="SANDBOX", base="http://127.0.0.1:7575")
        cli = wallet_cli.WalletCLI(client)
        with mock.patch.object(cli, "_workflow", return_value={}):
            with self.assertRaises(wallet_cli.WalletError) as raised:
                cli._mandate_cid(allow_stale=True)
        self.assertEqual(raised.exception.category, "MANDATE_CONTEXT_MISSING")


if __name__ == "__main__":
    unittest.main()
