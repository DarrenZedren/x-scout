from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from .config import DATA_DIR

AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
ME_URL = "https://api.x.com/2/users/me"
REDIRECT_URI = "http://127.0.0.1:8765/callback"
SCOPES = ["tweet.read", "users.read", "follows.read", "follows.write", "offline.access"]
STATE_PATH = DATA_DIR / "x_connection.json"
KEYRING_SERVICE = "X Scout"


class XAuthError(RuntimeError):
    pass


class CredentialVault:
    """Local credential vault with Windows DPAPI protection.

    On Windows, token material is encrypted for the current Windows user via DPAPI
    before it is written to data/.x_tokens.dat. On non-Windows systems a mode-0600
    local file is used as a compatibility fallback; no network service is involved.
    """

    def __init__(self, path: Path | None = None):
        self.path = Path(path or (DATA_DIR / ".x_tokens.dat"))
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _protect_windows(raw: bytes) -> bytes:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        in_buf = ctypes.create_string_buffer(raw)
        in_blob = DATA_BLOB(len(raw), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_byte)))
        out_blob = DATA_BLOB()
        crypt = ctypes.windll.crypt32.CryptProtectData
        crypt.argtypes = [ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
        crypt.restype = wintypes.BOOL
        if not crypt(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
            raise XAuthError("Windows could not protect X Scout credentials with DPAPI.")
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))

    @staticmethod
    def _unprotect_windows(raw: bytes) -> bytes:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        in_buf = ctypes.create_string_buffer(raw)
        in_blob = DATA_BLOB(len(raw), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_byte)))
        out_blob = DATA_BLOB()
        crypt = ctypes.windll.crypt32.CryptUnprotectData
        crypt.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
        crypt.restype = wintypes.BOOL
        if not crypt(ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)):
            raise XAuthError("Windows could not unlock X Scout credentials for this user.")
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            raw = self.path.read_bytes()
            if __import__("os").name == "nt":
                raw = self._unprotect_windows(raw)
            payload = json.loads(raw.decode("utf-8"))
            return {str(k): str(v) for k, v in payload.items()}
        except XAuthError:
            raise
        except Exception as exc:
            raise XAuthError(f"Could not read the local X Scout credential vault: {exc}") from exc

    def _save(self, payload: dict[str, str]) -> None:
        import os
        raw = json.dumps(payload, sort_keys=True).encode("utf-8")
        if os.name == "nt":
            raw = self._protect_windows(raw)
        self.path.write_bytes(raw)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def get(self, key: str) -> str:
        return self._load().get(key, "")

    def set(self, key: str, value: str) -> None:
        payload = self._load()
        payload[str(key)] = str(value)
        self._save(payload)

    def delete(self, key: str) -> None:
        payload = self._load()
        if key in payload:
            payload.pop(key, None)
            if payload:
                self._save(payload)
            else:
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass


@dataclass(slots=True)
class OAuthPending:
    verifier: str
    state: str
    started_at: float


class XAuthManager:
    """Local-only X OAuth 2.0 PKCE manager.

    The Client ID is ordinary app configuration and is stored in data/x_connection.json.
    Access/refresh tokens are stored in the operating system's credential vault. No X
    password is ever requested or stored by Scout.
    """

    def __init__(self, state_path: Path = STATE_PATH, vault: CredentialVault | None = None):
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.vault = vault or CredentialVault()
        self._lock = threading.RLock()
        self._pending: OAuthPending | None = None
        self._server: ThreadingHTTPServer | None = None
        self._server_thread: threading.Thread | None = None
        self._runtime_status = "disconnected"
        self._runtime_error = ""

    # ----- local state --------------------------------------------------
    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_state(self, payload: dict[str, Any]) -> None:
        self.state_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    @property
    def client_id(self) -> str:
        return str(self._load_state().get("client_id") or "").strip()

    def configure_client(self, client_id: str) -> str:
        client_id = str(client_id or "").strip()
        if len(client_id) < 8:
            raise XAuthError("Paste the OAuth 2.0 Client ID from your X Developer App.")
        state = self._load_state()
        old = str(state.get("client_id") or "")
        if old and old != client_id:
            self._clear_tokens(old)
            state.pop("account", None)
            state.pop("expires_at", None)
        state["client_id"] = client_id
        state["redirect_uri"] = REDIRECT_URI
        state["scopes"] = SCOPES
        self._save_state(state)
        return client_id

    def _vault_key(self, kind: str, client_id: str | None = None) -> str:
        cid = client_id or self.client_id
        return f"{kind}:{cid}"

    def _clear_tokens(self, client_id: str | None = None) -> None:
        cid = client_id or self.client_id
        if not cid:
            return
        self.vault.delete(self._vault_key("access", cid))
        self.vault.delete(self._vault_key("refresh", cid))

    def disconnect(self) -> dict[str, Any]:
        with self._lock:
            self._clear_tokens()
            state = self._load_state()
            state.pop("account", None)
            state.pop("expires_at", None)
            self._save_state(state)
            self._runtime_status = "disconnected"
            self._runtime_error = ""
            self._pending = None
            server = self._server
        if server:
            try:
                threading.Thread(target=server.shutdown, daemon=True).start()
            except Exception:
                pass
        return self.connection_state()

    # ----- PKCE ---------------------------------------------------------
    @staticmethod
    def make_verifier() -> str:
        # URL-safe, high-entropy and comfortably inside RFC 7636's 43-128 char range.
        return secrets.token_urlsafe(64)[:96]

    @staticmethod
    def challenge_for(verifier: str) -> str:
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    def authorization_url(self, client_id: str, verifier: str, state: str) -> str:
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "scope": " ".join(SCOPES),
            "state": state,
            "code_challenge": self.challenge_for(verifier),
            "code_challenge_method": "S256",
        }
        return f"{AUTHORIZE_URL}?{urlencode(params)}"

    # ----- connect flow -------------------------------------------------
    def begin_connect(self, client_id: str) -> dict[str, Any]:
        client_id = self.configure_client(client_id)
        with self._lock:
            if self._pending and (time.time() - self._pending.started_at) < 300:
                return {"ok": False, "message": "An X authorisation is already waiting in your browser."}

            verifier = self.make_verifier()
            state = secrets.token_urlsafe(32)
            self._pending = OAuthPending(verifier=verifier, state=state, started_at=time.time())
            self._runtime_status = "connecting"
            self._runtime_error = ""

            manager = self

            class CallbackHandler(BaseHTTPRequestHandler):
                def do_GET(self):  # noqa: N802
                    parsed = urlparse(self.path)
                    if parsed.path != "/callback":
                        self.send_response(404)
                        self.end_headers()
                        return
                    query = parse_qs(parsed.query)
                    error = str((query.get("error") or [""])[0])
                    code = str((query.get("code") or [""])[0])
                    returned_state = str((query.get("state") or [""])[0])
                    ok = False
                    message = "X Scout could not complete authorisation."
                    try:
                        if error:
                            raise XAuthError(f"X declined authorisation: {error}")
                        manager._complete_authorization(code, returned_state)
                        ok = True
                        message = "X Scout is connected. You can close this browser tab and return to Scout."
                    except Exception as exc:  # callback must always render a useful page
                        with manager._lock:
                            manager._runtime_status = "error"
                            manager._runtime_error = str(exc)
                            manager._pending = None
                        message = str(exc)
                    body = (
                        "<!doctype html><html><head><meta charset='utf-8'><title>X Scout</title>"
                        "<style>body{background:#02050a;color:#eaf7ff;font-family:Segoe UI,Arial,sans-serif;"
                        "display:grid;place-items:center;height:100vh;margin:0}.card{max-width:680px;padding:36px;"
                        "border:1px solid #1c5163;background:#071019}h1{letter-spacing:.08em}p{color:#a9c2cf;line-height:1.5}"
                        f".status{{color:{'#76ffbd' if ok else '#ff7188'}}}</style></head><body><div class='card'>"
                        f"<h1>X SCOUT</h1><p class='status'>{'LINK ESTABLISHED' if ok else 'LINK FAILED'}</p>"
                        f"<p>{message}</p></div></body></html>"
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    threading.Thread(target=self.server.shutdown, daemon=True).start()

                def log_message(self, fmt: str, *args: Any) -> None:
                    return

            try:
                self._server = ThreadingHTTPServer(("127.0.0.1", 8765), CallbackHandler)
            except OSError as exc:
                self._pending = None
                self._runtime_status = "error"
                self._runtime_error = f"Local callback port 8765 is unavailable: {exc}"
                return {"ok": False, "message": self._runtime_error}

            self._server_thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._server_thread.start()

            def expire_pending(expected_state: str, server: ThreadingHTTPServer) -> None:
                time.sleep(300)
                with self._lock:
                    if not self._pending or self._pending.state != expected_state:
                        return
                    self._pending = None
                    self._runtime_status = "error"
                    self._runtime_error = "X authorisation timed out. Press CONNECT X to try again."
                try:
                    server.shutdown()
                except Exception:
                    pass

            threading.Thread(target=expire_pending, args=(state, self._server), daemon=True).start()
            url = self.authorization_url(client_id, verifier, state)
            webbrowser.open(url)
            return {
                "ok": True,
                "message": "Browser opened. Authorise X Scout with X, then return here.",
                "redirectUri": REDIRECT_URI,
            }

    def _complete_authorization(self, code: str, returned_state: str) -> None:
        with self._lock:
            pending = self._pending
        if not pending:
            raise XAuthError("No X authorisation was pending.")
        if not code:
            raise XAuthError("X did not return an authorisation code.")
        if not secrets.compare_digest(returned_state or "", pending.state):
            raise XAuthError("OAuth state mismatch. Connection was rejected for safety.")
        token_payload = self._post_token({
            "code": code,
            "grant_type": "authorization_code",
            "client_id": self.client_id,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": pending.verifier,
        })
        self._store_token_payload(token_payload)
        account = self._fetch_me(self.get_access_token(refresh=False))
        state = self._load_state()
        state["account"] = account
        state["last_connected_at"] = datetime.now(timezone.utc).isoformat()
        self._save_state(state)
        with self._lock:
            self._pending = None
            self._runtime_status = "connected"
            self._runtime_error = ""

    # ----- token lifecycle ---------------------------------------------
    def _post_token(self, form: dict[str, str]) -> dict[str, Any]:
        data = urlencode(form).encode("utf-8")
        req = Request(
            TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json", "User-Agent": "X-Scout/0.4.0"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise XAuthError(f"X OAuth HTTP {exc.code}: {body[:500]}") from exc
        except URLError as exc:
            raise XAuthError(f"Could not reach X OAuth: {exc.reason}") from exc

    def _store_token_payload(self, payload: dict[str, Any]) -> None:
        access = str(payload.get("access_token") or "")
        refresh = str(payload.get("refresh_token") or "")
        if not access:
            raise XAuthError("X did not return an access token.")
        self.vault.set(self._vault_key("access"), access)
        if refresh:
            self.vault.set(self._vault_key("refresh"), refresh)
        state = self._load_state()
        expires_in = int(payload.get("expires_in") or 7200)
        state["expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=max(60, expires_in))).isoformat()
        state["granted_scope"] = str(payload.get("scope") or "")
        self._save_state(state)

    def refresh_access_token(self) -> str:
        refresh = self.vault.get(self._vault_key("refresh"))
        if not refresh:
            raise XAuthError("X session expired and no refresh token is available. Connect X again.")
        payload = self._post_token({
            "refresh_token": refresh,
            "grant_type": "refresh_token",
            "client_id": self.client_id,
        })
        self._store_token_payload(payload)
        return self.vault.get(self._vault_key("access"))

    def get_access_token(self, refresh: bool = True) -> str:
        if not self.client_id:
            raise XAuthError("X Developer Client ID has not been configured.")
        access = self.vault.get(self._vault_key("access"))
        if not access:
            raise XAuthError("X is not connected. Use X LINK first.")
        if refresh:
            state = self._load_state()
            raw_expiry = str(state.get("expires_at") or "")
            if raw_expiry:
                try:
                    expiry = datetime.fromisoformat(raw_expiry)
                    if expiry.tzinfo is None:
                        expiry = expiry.replace(tzinfo=timezone.utc)
                    if expiry <= datetime.now(timezone.utc) + timedelta(seconds=90):
                        access = self.refresh_access_token()
                except ValueError:
                    pass
        return access

    # ----- identity / status -------------------------------------------
    def _fetch_me(self, access_token: str) -> dict[str, Any]:
        params = urlencode({"user.fields": "id,name,username,verified,verified_type,public_metrics"})
        req = Request(
            f"{ME_URL}?{params}",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json", "User-Agent": "X-Scout/0.4.0"},
            method="GET",
        )
        try:
            with urlopen(req, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise XAuthError(f"X identity HTTP {exc.code}: {body[:500]}") from exc
        except URLError as exc:
            raise XAuthError(f"Could not reach X API: {exc.reason}") from exc
        data = payload.get("data") or {}
        if not data.get("id") or not data.get("username"):
            raise XAuthError("X connection did not return an authenticated account.")
        return {
            "id": str(data.get("id") or ""),
            "username": str(data.get("username") or ""),
            "name": str(data.get("name") or ""),
            "verified": bool(data.get("verified")),
            "verified_type": str(data.get("verified_type") or "none"),
        }

    def test_connection(self) -> dict[str, Any]:
        access = self.get_access_token(refresh=True)
        try:
            account = self._fetch_me(access)
        except XAuthError as exc:
            # A 401 can be caused by an expired access token whose timestamp was stale.
            if "HTTP 401" not in str(exc):
                raise
            access = self.refresh_access_token()
            account = self._fetch_me(access)
        state = self._load_state()
        state["account"] = account
        state["last_tested_at"] = datetime.now(timezone.utc).isoformat()
        self._save_state(state)
        with self._lock:
            self._runtime_status = "connected"
            self._runtime_error = ""
        return self.connection_state()

    def is_connected(self) -> bool:
        if not self.client_id:
            return False
        try:
            return bool(self.vault.get(self._vault_key("access")))
        except XAuthError:
            return False

    def connection_state(self) -> dict[str, Any]:
        state = self._load_state()
        configured = bool(self.client_id)
        connected = self.is_connected()
        account = state.get("account") or {}
        with self._lock:
            status = self._runtime_status
            error = self._runtime_error
            pending = bool(self._pending)
        if connected and status not in {"connecting", "error"}:
            status = "connected"
        elif not connected and status == "connected":
            status = "disconnected"
        return {
            "configured": configured,
            "connected": connected,
            "connecting": pending or status == "connecting",
            "status": status,
            "error": error,
            "clientId": self.client_id,
            "redirectUri": REDIRECT_URI,
            "scopes": list(SCOPES),
            "account": account,
            "storage": "Windows DPAPI local vault" if __import__("os").name == "nt" else "local mode-0600 vault",
        }
