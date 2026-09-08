from __future__ import annotations

import json
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import RawAuthor
from .x_auth import XAuthManager, XAuthError

Progress = Callable[[int, str], None]


class XClientError(RuntimeError):
    pass


class XUserClient:
    BASE = "https://api.x.com/2"

    def __init__(self, auth: XAuthManager):
        self.auth = auth

    def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json_body: dict[str, Any] | None = None) -> dict[str, Any]:
        token = self.auth.get_access_token(refresh=True)
        url = f"{self.BASE}{path}"
        if params:
            clean = {k: v for k, v in params.items() if v is not None and v != ""}
            url = f"{url}?{urlencode(clean)}"
        body = None
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": "X-Scout/0.4.0"}
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = Request(url, data=body, headers=headers, method=method)
        try:
            with urlopen(req, timeout=35) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            if exc.code == 401:
                # Retry once after forced refresh.
                try:
                    self.auth.refresh_access_token()
                except XAuthError:
                    pass
                else:
                    token = self.auth.get_access_token(refresh=False)
                    headers["Authorization"] = f"Bearer {token}"
                    req2 = Request(url, data=body, headers=headers, method=method)
                    try:
                        with urlopen(req2, timeout=35) as response:
                            raw2 = response.read().decode("utf-8")
                            return json.loads(raw2) if raw2 else {}
                    except HTTPError as exc2:
                        raw = exc2.read().decode("utf-8", errors="replace")
                        raise XClientError(f"X API HTTP {exc2.code}: {raw[:500]}") from exc2
            raise XClientError(f"X API HTTP {exc.code}: {raw[:500]}") from exc
        except URLError as exc:
            raise XClientError(f"Could not reach X API: {exc.reason}") from exc

    def me(self) -> dict[str, Any]:
        return (self._request("GET", "/users/me", params={"user.fields": "id,name,username,verified,verified_type,public_metrics"}).get("data") or {})

    def lookup_username(self, username: str) -> dict[str, Any]:
        username = str(username or "").strip().lstrip("@")
        if not username:
            raise XClientError("Username is required.")
        return (self._request("GET", f"/users/by/username/{username}", params={"user.fields": "id,name,username,description,verified,verified_type,public_metrics,location"}).get("data") or {})

    @staticmethod
    def _raw_author(user: dict[str, Any]) -> RawAuthor:
        return RawAuthor.from_api(user)

    def _user_list(self, path: str, progress: Progress | None = None, label: str = "network") -> list[RawAuthor]:
        out: dict[str, RawAuthor] = {}
        token = ""
        page = 0
        while True:
            page += 1
            payload = self._request(
                "GET",
                path,
                params={
                    "max_results": 1000,
                    "pagination_token": token or None,
                    "user.fields": "id,name,username,description,verified,verified_type,public_metrics,location",
                },
            )
            for raw in payload.get("data") or []:
                author = self._raw_author(raw)
                if author.user_id and author.username:
                    out[author.user_id] = author
            if progress:
                progress(page, f"{label} // {len(out)} accounts resolved")
            token = str((payload.get("meta") or {}).get("next_token") or "")
            if not token:
                break
            if page >= 250:  # hard safety rail; 250,000 accounts would already be extreme for Scout.
                raise XClientError(f"{label} pagination safety limit reached.")
        return list(out.values())

    def following(self, user_id: str, progress: Progress | None = None) -> list[RawAuthor]:
        return self._user_list(f"/users/{user_id}/following", progress, "following")

    def followers(self, user_id: str, progress: Progress | None = None) -> list[RawAuthor]:
        return self._user_list(f"/users/{user_id}/followers", progress, "followers")

    def follow(self, source_user_id: str, target_user_id: str) -> dict[str, Any]:
        return self._request("POST", f"/users/{source_user_id}/following", json_body={"target_user_id": str(target_user_id)})

    def unfollow(self, source_user_id: str, target_user_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/users/{source_user_id}/following/{target_user_id}")
