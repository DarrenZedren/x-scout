from __future__ import annotations

import json
import threading
import traceback
import webbrowser
from datetime import datetime
from typing import Any

from .config import load_config
from .database import ScoutDatabase
from .engine import ScoutEngine
from .models import ConversationCandidate, PersonCandidate
from .providers import MockProvider, XApiProvider
from .scoring import age_hours
from .topic_profiles import TopicProfile, analyse_description, query_groups_for_terms
from .x_auth import XAuthError, XAuthManager
from .x_client import XClientError, XUserClient

VERSION = "0.4.0"


def _age_label(dt: datetime) -> str:
    h = age_hours(dt)
    if h < 1:
        return f"{max(1, round(h * 60))}m"
    return f"{h:.1f}h"


def _person_json(c: PersonCandidate, relationship: str) -> dict[str, Any]:
    a = c.author
    return {
        "id": a.user_id,
        "username": a.username,
        "name": a.name,
        "description": a.description,
        "verifiedType": a.verified_type,
        "followers": a.followers_count,
        "score": c.score,
        "reason": c.reason,
        "age": _age_label(c.latest_post_at),
        "relationship": relationship,
        "url": c.profile_url,
        "decision": c.decision,
        "topics": [p.text[:160] for p in c.matched_posts[:2]],
    }


def _conversation_json(c: ConversationCandidate, relationship: str) -> dict[str, Any]:
    return {
        "id": c.post.post_id,
        "username": c.author.username,
        "name": c.author.name,
        "verifiedType": c.author.verified_type,
        "followers": c.author.followers_count,
        "score": c.score,
        "reason": c.reason,
        "age": _age_label(c.post.created_at),
        "relationship": relationship,
        "replyCount": c.post.reply_count,
        "likeCount": c.post.like_count,
        "text": c.post.text,
        "url": c.post_url,
        "decision": c.decision,
    }


class ScoutBridge:
    """Python API exposed to the local HTML cockpit by pywebview."""

    def __init__(self) -> None:
        self.db = ScoutDatabase()
        self.auth = XAuthManager()
        self._lock = threading.RLock()
        self._scan: dict[str, Any] = {
            "running": False,
            "progress": 0,
            "message": "RADAR STANDBY",
            "error": "",
            "people": [],
            "conversations": [],
            "mode": "demo",
            "signals": 0,
        }
        self._people_objects: dict[str, PersonCandidate] = {}
        self._conversation_objects: dict[str, ConversationCandidate] = {}
        self._network_synced_this_session = False

    def _live(self) -> bool:
        return self.auth.is_connected()

    def _mission_config(self) -> dict[str, Any]:
        cfg = load_config()
        p = self.db.active_profile()
        cfg["profile_name"] = p.name
        cfg["wanted_terms"] = p.wanted_terms
        cfg["blocked_terms"] = p.blocked_terms
        cfg["activity_terms"] = p.activity_terms
        cfg["query_groups"] = p.query_groups
        cfg["taste_feedback"] = self.db.feedback_counts()
        return cfg

    def app_state(self) -> dict[str, Any]:
        active = self.db.active_profile()
        connection = self.auth.connection_state()
        return {
            "version": VERSION,
            "mode": "live" if connection["connected"] else "demo",
            "socialMode": "live-human-click" if connection["connected"] else "demo-local",
            "connection": connection,
            "profiles": self.db.list_profiles(),
            "activeProfile": self.profile(active.name),
            "networkStats": self.db.network_stats(),
            "lastScan": self.db.last_scan_summary(),
            "rules": {
                "windowHours": 24,
                "strongHours": 12,
                "highestHours": 6,
                "graceDays": int(load_config().get("network", {}).get("grace_days", 7)),
                "qualityPeople": float(load_config().get("quality_floor", {}).get("people", 0)),
                "qualityConversations": float(load_config().get("quality_floor", {}).get("conversations", 0)),
                "humanInCommand": True,
                "noCandidateCache": True,
            },
            "seen": self.db.seen_counts(active.name),
            "tasteFeedback": self.db.feedback_counts(),
        }

    # ---- X connection --------------------------------------------------
    def x_connection_state(self) -> dict[str, Any]:
        return self.auth.connection_state()

    def x_begin_connect(self, client_id: str) -> dict[str, Any]:
        try:
            return self.auth.begin_connect(client_id)
        except XAuthError as exc:
            return {"ok": False, "message": str(exc)}

    def x_test_connection(self) -> dict[str, Any]:
        try:
            state = self.auth.test_connection()
            return {"ok": True, "message": f"Connected as @{state.get('account', {}).get('username', '')}", "state": state}
        except XAuthError as exc:
            return {"ok": False, "message": str(exc), "state": self.auth.connection_state()}

    def x_disconnect(self) -> dict[str, Any]:
        state = self.auth.disconnect()
        self._network_synced_this_session = False
        return {"ok": True, "message": "X disconnected on this computer.", "state": state}

    def _sync_live_network_or_raise(self, progress=None) -> tuple[int, int]:
        client = XUserClient(self.auth)
        me = client.me()
        user_id = str(me.get("id") or "")
        if not user_id:
            raise XClientError("Authenticated X account ID was not returned.")
        following = client.following(user_id, progress)
        followers = client.followers(user_id, progress)
        counts = self.db.sync_live_network(following, followers)
        self._network_synced_this_session = True
        return counts

    def x_sync_network(self) -> dict[str, Any]:
        if not self._live():
            return {"ok": False, "message": "Connect X first."}
        try:
            n_following, n_followers = self._sync_live_network_or_raise()
            return {
                "ok": True,
                "message": f"Network synced // {n_following} following / {n_followers} followers",
                "stats": self.db.network_stats(),
                "rows": self.network_rows(),
            }
        except (XClientError, XAuthError) as exc:
            return {"ok": False, "message": str(exc)}

    # ---- topic profiles ------------------------------------------------
    def profile(self, name: str) -> dict[str, Any] | None:
        p = self.db.get_profile(name)
        if not p:
            return None
        return {
            "name": p.name,
            "description": p.description,
            "wantedTerms": p.wanted_terms,
            "blockedTerms": p.blocked_terms,
            "activityTerms": p.activity_terms,
            "queryGroups": p.query_groups,
        }

    def activate_profile(self, name: str) -> dict[str, Any]:
        self.db.activate_profile(name)
        return self.app_state()

    def analyse_topics(self, description: str, exclusions: str = "") -> dict[str, Any]:
        p = analyse_description(description or "", exclusions or "")
        return {
            "name": p.name,
            "description": p.description,
            "wantedTerms": p.wanted_terms,
            "blockedTerms": p.blocked_terms,
            "activityTerms": p.activity_terms,
            "queryGroups": p.query_groups,
        }

    def save_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "Custom Scout").strip()[:60]
        wanted_terms = [str(x).strip() for x in payload.get("wantedTerms", []) if str(x).strip()]
        p = TopicProfile(
            name=name,
            description=str(payload.get("description") or "Custom X Scout profile").strip(),
            wanted_terms=wanted_terms,
            blocked_terms=[str(x).strip() for x in payload.get("blockedTerms", []) if str(x).strip()],
            activity_terms=[str(x).strip() for x in payload.get("activityTerms", []) if str(x).strip()],
            query_groups=query_groups_for_terms(wanted_terms),
        )
        self.db.save_profile(p, name=name, activate=True)
        return self.app_state()

    # ---- scan -----------------------------------------------------------
    def start_scan(self) -> dict[str, Any]:
        live = self._live()
        with self._lock:
            if self._scan["running"]:
                return dict(self._scan)
            self._scan = {
                "running": True,
                "progress": 1,
                "message": "IGNITION // locking mission profile",
                "error": "",
                "people": [],
                "conversations": [],
                "mode": "live" if live else "demo",
                "signals": 0,
            }
        threading.Thread(target=self._run_scan, daemon=True).start()
        return dict(self._scan)

    def _run_scan(self) -> None:
        try:
            cfg = self._mission_config()
            if self._live():
                if not self._network_synced_this_session:
                    with self._lock:
                        self._scan["progress"] = 4
                        self._scan["message"] = "LIVE X // synchronising followers + following"
                    def network_progress(page, message):
                        with self._lock:
                            self._scan["progress"] = 4
                            self._scan["message"] = f"LIVE X // {message}"
                    self._sync_live_network_or_raise(network_progress)
                provider = XApiProvider(self.auth.get_access_token(refresh=True), cfg)
                mode = "live"
            else:
                provider = MockProvider(cfg)
                mode = "demo"
            engine = ScoutEngine(provider, cfg, self.db)

            def progress(pct: int, message: str) -> None:
                import re
                with self._lock:
                    self._scan["progress"] = int(pct)
                    self._scan["message"] = str(message)
                    for pattern in (r"(\d+) current signals", r"(\d+) unique posts", r"(\d+) signals from"):
                        m = re.search(pattern, str(message))
                        if m:
                            self._scan["signals"] = int(m.group(1))
                            break

            people, conversations = engine.scan(mode, progress)
            self._people_objects = {c.author.user_id: c for c in people}
            self._conversation_objects = {c.post.post_id: c for c in conversations}
            people_json = [_person_json(c, self.db.relationship(c.author.username)) for c in people]
            conv_json = [_conversation_json(c, self.db.relationship(c.author.username)) for c in conversations]
            with self._lock:
                self._scan.update({
                    "running": False,
                    "progress": 100,
                    "message": f"LOCK ACQUIRED // {len(people)} people + {len(conversations)} conversations",
                    "people": people_json,
                    "conversations": conv_json,
                    "mode": mode,
                })
        except Exception as exc:
            with self._lock:
                self._scan.update({
                    "running": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "message": "MISSION ABORTED",
                })
            traceback.print_exc()

    def scan_state(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._scan))

    # ---- human-controlled actions --------------------------------------
    def follow_person(self, user_id: str) -> dict[str, Any]:
        c = self._people_objects.get(str(user_id))
        if not c:
            return {"ok": False, "message": "Candidate is not in the current shortlist."}
        try:
            if self._live():
                account = self.auth.connection_state().get("account") or {}
                source_user_id = str(account.get("id") or "")
                if not source_user_id:
                    account = XUserClient(self.auth).me()
                    source_user_id = str(account.get("id") or "")
                XUserClient(self.auth).follow(source_user_id, c.author.user_id)
                source = "x-live"
                message = f"@{c.author.username} followed on X. Human click executed."
            else:
                source = "scout-demo"
                message = f"@{c.author.username} added to local demo Following. Human click recorded."
            self.db.follow_author(c.author, source=source)
            with self._lock:
                self._scan["people"] = [p for p in self._scan.get("people", []) if p.get("id") != str(user_id)]
            return {"ok": True, "message": message, "stats": self.db.network_stats()}
        except (XClientError, XAuthError) as exc:
            return {"ok": False, "message": str(exc)}

    def unfollow(self, username: str) -> dict[str, Any]:
        username = str(username or "").strip().lstrip("@")
        try:
            if self._live():
                client = XUserClient(self.auth)
                account = self.auth.connection_state().get("account") or client.me()
                source_user_id = str(account.get("id") or "")
                target_id = self.db.network_user_id(username)
                if not target_id:
                    target = client.lookup_username(username)
                    target_id = str(target.get("id") or "")
                if not source_user_id or not target_id:
                    raise XClientError("Could not resolve the X user IDs required to unfollow.")
                client.unfollow(source_user_id, target_id)
                message = f"@{username} unfollowed on X. Human click executed."
            else:
                message = f"@{username} removed from local demo Following. Human click recorded."
            self.db.unfollow(username)
            return {"ok": True, "message": message, "stats": self.db.network_stats()}
        except (XClientError, XAuthError) as exc:
            return {"ok": False, "message": str(exc)}

    def set_protected(self, username: str, protected: bool) -> dict[str, Any]:
        self.db.set_protected(username, bool(protected))
        verb = "protected" if protected else "returned to reciprocity review"
        return {"ok": True, "message": f"@{username} {verb}.", "stats": self.db.network_stats()}

    def decision(self, target_type: str, target_id: str, decision: str, reason: str = "") -> dict[str, Any]:
        self.db.set_decision(target_type, target_id, decision, reason)
        if target_type == "person" and decision in {"not_for_me", "never_show"}:
            with self._lock:
                self._scan["people"] = [p for p in self._scan.get("people", []) if p.get("id") != str(target_id)]
                person = self._people_objects.get(str(target_id))
                if person:
                    username = person.author.username.lower()
                    self._scan["conversations"] = [
                        c for c in self._scan.get("conversations", [])
                        if str(c.get("username", "")).lower() != username
                    ]
        elif target_type == "conversation" and decision in {"not_for_me", "never_show"}:
            with self._lock:
                self._scan["conversations"] = [
                    c for c in self._scan.get("conversations", []) if c.get("id") != str(target_id)
                ]
        return {"ok": True, "tasteFeedback": self.db.feedback_counts()}

    def reset_seen(self) -> dict[str, Any]:
        profile = self.db.active_profile().name
        removed = self.db.reset_seen(profile)
        return {"ok": True, "profile": profile, "removed": removed, "seen": self.db.seen_counts(profile)}

    def import_network(self, following_text: str, followers_text: str) -> dict[str, Any]:
        following, followers = self.db.import_network_lists(following_text or "", followers_text or "")
        return {
            "ok": True,
            "followingImported": following,
            "followersImported": followers,
            "stats": self.db.network_stats(),
            "rows": self.network_rows(),
        }

    def network_rows(self) -> list[dict[str, Any]]:
        rows = self.db.network_rows()
        out: list[dict[str, Any]] = []
        for r in rows:
            status = str(r.get("status", "NONE"))
            if status == "REVIEW":
                recommendation = "Grace expired — review for unfollow"
            elif status == "GRACE":
                recommendation = "Waiting for follow-back"
            elif status == "PROTECTED":
                recommendation = "Keep following regardless of reciprocity"
            elif status == "MUTUAL":
                recommendation = "Mutual connection"
            elif status == "FOLLOWS YOU":
                recommendation = "Follows you — optional follow-back"
            else:
                recommendation = "No active relationship"
            out.append({
                "userId": str(r.get("user_id", "")),
                "username": str(r.get("username", "")),
                "name": str(r.get("display_name", "")),
                "youFollow": bool(r.get("you_follow")),
                "followsYou": bool(r.get("follows_you")),
                "protected": bool(r.get("protected")),
                "status": status,
                "ageDays": r.get("age_days"),
                "source": str(r.get("source", "")),
                "recommendation": recommendation,
            })
        return out

    def open_url(self, url: str) -> dict[str, Any]:
        if not isinstance(url, str) or not url.startswith("https://x.com/"):
            return {"ok": False, "message": "Only x.com links are opened by Scout."}
        webbrowser.open(url)
        return {"ok": True}

    def open_external(self, url: str) -> dict[str, Any]:
        allowed = ("https://console.x.com/", "https://docs.x.com/")
        if not isinstance(url, str) or not url.startswith(allowed):
            return {"ok": False, "message": "External link blocked by Scout."}
        webbrowser.open(url)
        return {"ok": True}
