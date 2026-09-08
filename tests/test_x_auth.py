import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from x_scout.x_auth import REDIRECT_URI, SCOPES, XAuthError, XAuthManager


class FakeVault:
    def __init__(self):
        self.data = {}
    def get(self, key):
        return self.data.get(key, "")
    def set(self, key, value):
        self.data[key] = value
    def delete(self, key):
        self.data.pop(key, None)


class XAuthTests(unittest.TestCase):
    def test_authorization_url_is_pkce_s256_and_requests_required_scopes(self):
        with tempfile.TemporaryDirectory() as td:
            auth = XAuthManager(state_path=Path(td) / "x_connection.json", vault=FakeVault())
            verifier = "v" * 64
            url = auth.authorization_url("client-12345678", verifier, "state-abc")
            parsed = urlparse(url)
            q = parse_qs(parsed.query)
            self.assertEqual(q["client_id"][0], "client-12345678")
            self.assertEqual(q["redirect_uri"][0], REDIRECT_URI)
            self.assertEqual(q["code_challenge_method"][0], "S256")
            self.assertNotEqual(q["code_challenge"][0], verifier)
            self.assertEqual(set(q["scope"][0].split()), set(SCOPES))

    def test_tokens_stay_in_vault_not_connection_json(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "x_connection.json"
            vault = FakeVault()
            auth = XAuthManager(state_path=path, vault=vault)
            auth.configure_client("client-12345678")
            auth._store_token_payload({"access_token": "secret-access", "refresh_token": "secret-refresh", "expires_in": 3600})
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("secret-access", raw)
            self.assertNotIn("secret-refresh", raw)
            self.assertEqual(vault.get("access:client-12345678"), "secret-access")
            self.assertEqual(vault.get("refresh:client-12345678"), "secret-refresh")

    def test_complete_authorization_rejects_state_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            auth = XAuthManager(state_path=Path(td) / "x_connection.json", vault=FakeVault())
            auth.configure_client("client-12345678")
            from x_scout.x_auth import OAuthPending
            auth._pending = OAuthPending(verifier="x" * 64, state="expected", started_at=0)
            with self.assertRaises(XAuthError):
                auth._complete_authorization("code", "wrong")

    def test_disconnect_clears_local_tokens_but_keeps_client_id(self):
        with tempfile.TemporaryDirectory() as td:
            vault = FakeVault()
            auth = XAuthManager(state_path=Path(td) / "x_connection.json", vault=vault)
            auth.configure_client("client-12345678")
            auth._store_token_payload({"access_token": "a", "refresh_token": "r", "expires_in": 3600})
            self.assertTrue(auth.is_connected())
            state = auth.disconnect()
            self.assertFalse(state["connected"])
            self.assertEqual(state["clientId"], "client-12345678")


if __name__ == "__main__":
    unittest.main()
