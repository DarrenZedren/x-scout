from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .config import DB_PATH
from .models import ConversationCandidate, PersonCandidate, RawAuthor
from .topic_profiles import TopicProfile, preset_profiles


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS scans (
    scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    mode TEXT NOT NULL,
    people_found INTEGER NOT NULL DEFAULT 0,
    conversations_found INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS people (
    user_id TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    verified_type TEXT NOT NULL,
    followers_count INTEGER NOT NULL,
    latest_post_at TEXT NOT NULL,
    score REAL NOT NULL,
    reason TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    raw_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS conversations (
    post_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    username TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    reply_count INTEGER NOT NULL,
    like_count INTEGER NOT NULL,
    score REAL NOT NULL,
    reason TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    raw_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS decisions (
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (target_type, target_id)
);

CREATE TABLE IF NOT EXISTS seen (
    profile_name TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    PRIMARY KEY (profile_name, target_type, target_id)
);

CREATE TABLE IF NOT EXISTS topic_profiles (
    name TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    wanted_json TEXT NOT NULL,
    blocked_json TEXT NOT NULL,
    activity_json TEXT NOT NULL,
    query_groups_json TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS network (
    username TEXT PRIMARY KEY COLLATE NOCASE,
    display_name TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL DEFAULT '',
    you_follow INTEGER NOT NULL DEFAULT 0,
    follows_you INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    followed_at TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    protected INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
"""


class ScoutDatabase:
    def __init__(self, path: Path = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()
        self._seed_profiles()
        self._seed_demo_network_if_empty()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=20)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with closing(self.connect()) as conn, conn:
            conn.executescript(SCHEMA)
            cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(network)").fetchall()}
            if "protected" not in cols:
                conn.execute("ALTER TABLE network ADD COLUMN protected INTEGER NOT NULL DEFAULT 0")
            decision_cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(decisions)").fetchall()}
            if "reason" not in decision_cols:
                conn.execute("ALTER TABLE decisions ADD COLUMN reason TEXT NOT NULL DEFAULT ''")

    def _seed_profiles(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with closing(self.connect()) as conn, conn:
            existing = conn.execute("SELECT COUNT(*) AS n FROM topic_profiles").fetchone()["n"]
            if existing:
                return
            for idx, p in enumerate(preset_profiles()):
                conn.execute(
                    """INSERT INTO topic_profiles(name, description, wanted_json, blocked_json, activity_json,
                                                   query_groups_json, active, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (p.name, p.description, json.dumps(p.wanted_terms), json.dumps(p.blocked_terms),
                     json.dumps(p.activity_terms), json.dumps(p.query_groups), 1 if idx == 0 else 0, now),
                )

    def _seed_demo_network_if_empty(self) -> None:
        with closing(self.connect()) as conn, conn:
            n = conn.execute("SELECT COUNT(*) AS n FROM network").fetchone()["n"]
            if n:
                return
        demo = [
            ("orbit_maya_demo", "Maya Vale", 1, 1, 12),
            ("stack_theo_demo", "Theo Walsh", 1, 1, 18),
            ("tinyflows_demo", "Tiny Flows", 1, 1, 30),
            ("signal_priya_demo", "Priya Rao", 1, 0, 2),
            ("maker_owen_demo", "Owen Grant", 1, 0, 5),
            ("nina_systems_demo", "Nina Park", 1, 0, 9),
            ("julesbuilds_demo", "Jules Reed", 1, 0, 14),
            ("fred_follows_demo", "Fred Quinn", 0, 1, 20),
            ("lena_follows_demo", "Lena Chen", 0, 1, 16),
        ]
        now = datetime.now(timezone.utc)
        with closing(self.connect()) as conn, conn:
            for username, name, you_follow, follows_you, days in demo:
                first = (now.replace(microsecond=0) - __import__("datetime").timedelta(days=days)).isoformat()
                followed = first if you_follow else None
                conn.execute(
                    """INSERT OR IGNORE INTO network(username, display_name, you_follow, follows_you,
                                                     first_seen_at, followed_at, source, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'demo', ?)""",
                    (username, name, you_follow, follows_you, first, followed, now.isoformat()),
                )

    # scans / results -----------------------------------------------------
    def start_scan(self, mode: str) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with closing(self.connect()) as conn, conn:
            cur = conn.execute("INSERT INTO scans(started_at, mode, status) VALUES (?, ?, 'running')", (now, mode))
            return int(cur.lastrowid)

    def finish_scan(self, scan_id: int, people: int, conversations: int, status: str = "complete") -> None:
        now = datetime.now(timezone.utc).isoformat()
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "UPDATE scans SET completed_at=?, people_found=?, conversations_found=?, status=? WHERE scan_id=?",
                (now, people, conversations, status, scan_id),
            )

    def save_people(self, people: list[PersonCandidate]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with closing(self.connect()) as conn, conn:
            for c in people:
                a = c.author
                conn.execute(
                    """INSERT INTO people(user_id, username, name, description, verified_type, followers_count,
                                          latest_post_at, score, reason, last_seen_at, raw_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, name=excluded.name,
                           description=excluded.description, verified_type=excluded.verified_type,
                           followers_count=excluded.followers_count, latest_post_at=excluded.latest_post_at,
                           score=excluded.score, reason=excluded.reason, last_seen_at=excluded.last_seen_at,
                           raw_json=excluded.raw_json""",
                    (a.user_id, a.username, a.name, a.description, a.verified_type, a.followers_count,
                     c.latest_post_at.isoformat(), c.score, c.reason, now,
                     json.dumps({"location": a.location, "post_count": a.post_count})),
                )

    def save_conversations(self, items: list[ConversationCandidate]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with closing(self.connect()) as conn, conn:
            for c in items:
                p, a = c.post, c.author
                conn.execute(
                    """INSERT INTO conversations(post_id, user_id, username, text, created_at, reply_count,
                                                 like_count, score, reason, last_seen_at, raw_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(post_id) DO UPDATE SET username=excluded.username, text=excluded.text,
                           created_at=excluded.created_at, reply_count=excluded.reply_count,
                           like_count=excluded.like_count, score=excluded.score, reason=excluded.reason,
                           last_seen_at=excluded.last_seen_at, raw_json=excluded.raw_json""",
                    (p.post_id, a.user_id, a.username, p.text, p.created_at.isoformat(), p.reply_count,
                     p.like_count, c.score, c.reason, now,
                     json.dumps({"followers_count": a.followers_count, "verified_type": a.verified_type})),
                )

    def set_decision(self, target_type: str, target_id: str, decision: str, reason: str = "") -> None:
        if decision not in {"keep", "ignore", "not_for_me", "never_show", "unreviewed"}:
            raise ValueError(f"Unknown decision: {decision}")
        allowed_reasons = {
            "", "spammy", "too_salesy", "ragebait", "crypto_rubbish", "low_quality",
            "not_builder", "engagement_farmer", "too_corporate", "other",
        }
        reason = str(reason or "").strip().lower()
        if reason not in allowed_reasons:
            reason = "other"
        now = datetime.now(timezone.utc).isoformat()
        with closing(self.connect()) as conn, conn:
            conn.execute(
                """INSERT INTO decisions(target_type, target_id, decision, reason, updated_at) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(target_type, target_id) DO UPDATE SET decision=excluded.decision,
                                                                  reason=excluded.reason,
                                                                  updated_at=excluded.updated_at""",
                (target_type, target_id, decision, reason, now),
            )

    def get_decisions(self, target_type: str) -> dict[str, str]:
        with closing(self.connect()) as conn, conn:
            rows = conn.execute("SELECT target_id, decision FROM decisions WHERE target_type=?", (target_type,)).fetchall()
        return {str(r["target_id"]): str(r["decision"]) for r in rows}

    def feedback_counts(self) -> dict[str, int]:
        with closing(self.connect()) as conn:
            rows = conn.execute(
                """SELECT reason, COUNT(*) AS n FROM decisions
                   WHERE target_type='person' AND decision='not_for_me' AND reason<>''
                   GROUP BY reason"""
            ).fetchall()
        return {str(r["reason"]): int(r["n"]) for r in rows}

    # per-profile presentation memory ------------------------------------
    def seen_ids(self, profile_name: str, target_type: str) -> set[str]:
        with closing(self.connect()) as conn:
            rows = conn.execute(
                "SELECT target_id FROM seen WHERE profile_name=? AND target_type=?",
                (profile_name, target_type),
            ).fetchall()
        return {str(r["target_id"]) for r in rows}

    def mark_seen(self, profile_name: str, target_type: str, target_ids) -> None:
        ids = [str(x) for x in target_ids if str(x)]
        if not ids:
            return
        now = datetime.now(timezone.utc).isoformat()
        with closing(self.connect()) as conn, conn:
            conn.executemany(
                "INSERT OR IGNORE INTO seen(profile_name, target_type, target_id, seen_at) VALUES (?, ?, ?, ?)",
                [(profile_name, target_type, target_id, now) for target_id in ids],
            )

    def reset_seen(self, profile_name: str) -> int:
        with closing(self.connect()) as conn, conn:
            cur = conn.execute("DELETE FROM seen WHERE profile_name=?", (profile_name,))
            return int(cur.rowcount or 0)

    def seen_counts(self, profile_name: str) -> dict[str, int]:
        with closing(self.connect()) as conn:
            rows = conn.execute(
                "SELECT target_type, COUNT(*) AS n FROM seen WHERE profile_name=? GROUP BY target_type",
                (profile_name,),
            ).fetchall()
        out = {"person": 0, "conversation": 0}
        for r in rows:
            out[str(r["target_type"])] = int(r["n"])
        return out

    def last_scan_summary(self) -> dict | None:
        with closing(self.connect()) as conn, conn:
            row = conn.execute("SELECT * FROM scans WHERE status='complete' ORDER BY scan_id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    # topic profiles ------------------------------------------------------
    def list_profiles(self) -> list[str]:
        with closing(self.connect()) as conn:
            rows = conn.execute("SELECT name FROM topic_profiles ORDER BY active DESC, name COLLATE NOCASE").fetchall()
        return [str(r["name"]) for r in rows]

    def active_profile(self) -> TopicProfile:
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT * FROM topic_profiles ORDER BY active DESC, name LIMIT 1").fetchone()
        if not row:
            raise RuntimeError("No topic profiles available")
        return self._profile_from_row(row)

    def get_profile(self, name: str) -> TopicProfile | None:
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT * FROM topic_profiles WHERE name=?", (name,)).fetchone()
        return self._profile_from_row(row) if row else None

    @staticmethod
    def _profile_from_row(row) -> TopicProfile:
        return TopicProfile(
            name=str(row["name"]), description=str(row["description"]),
            wanted_terms=json.loads(row["wanted_json"]), blocked_terms=json.loads(row["blocked_json"]),
            activity_terms=json.loads(row["activity_json"]), query_groups=json.loads(row["query_groups_json"]),
        )

    def save_profile(self, profile: TopicProfile, name: str | None = None, activate: bool = True) -> str:
        final_name = (name or profile.name or "Custom Scout").strip()[:60]
        now = datetime.now(timezone.utc).isoformat()
        with closing(self.connect()) as conn, conn:
            if activate:
                conn.execute("UPDATE topic_profiles SET active=0")
            conn.execute(
                """INSERT INTO topic_profiles(name, description, wanted_json, blocked_json, activity_json,
                                              query_groups_json, active, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET description=excluded.description,
                       wanted_json=excluded.wanted_json, blocked_json=excluded.blocked_json,
                       activity_json=excluded.activity_json, query_groups_json=excluded.query_groups_json,
                       active=excluded.active, updated_at=excluded.updated_at""",
                (final_name, profile.description, json.dumps(profile.wanted_terms), json.dumps(profile.blocked_terms),
                 json.dumps(profile.activity_terms), json.dumps(profile.query_groups), 1 if activate else 0, now),
            )
        return final_name

    def activate_profile(self, name: str) -> None:
        with closing(self.connect()) as conn, conn:
            conn.execute("UPDATE topic_profiles SET active=0")
            conn.execute("UPDATE topic_profiles SET active=1 WHERE name=?", (name,))

    # network -------------------------------------------------------------
    @staticmethod
    def extract_handles(text: str) -> set[str]:
        handles = {m.lower() for m in re.findall(r"@([A-Za-z0-9_]{1,15})", text or "")}
        handles.update(m.lower() for m in re.findall(r"(?:x\.com|twitter\.com)/([A-Za-z0-9_]{1,15})", text or "", re.I))
        return handles

    def import_network_lists(self, following_text: str, followers_text: str) -> tuple[int, int]:
        following = self.extract_handles(following_text)
        followers = self.extract_handles(followers_text)
        all_handles = following | followers
        now = datetime.now(timezone.utc).isoformat()
        with closing(self.connect()) as conn, conn:
            for handle in all_handles:
                existing = conn.execute("SELECT first_seen_at, followed_at FROM network WHERE username=?", (handle,)).fetchone()
                first_seen = existing["first_seen_at"] if existing else now
                followed_at = existing["followed_at"] if existing and existing["followed_at"] else (now if handle in following else None)
                conn.execute(
                    """INSERT INTO network(username, you_follow, follows_you, first_seen_at, followed_at, source, updated_at)
                       VALUES (?, ?, ?, ?, ?, 'import', ?)
                       ON CONFLICT(username) DO UPDATE SET you_follow=excluded.you_follow,
                           follows_you=excluded.follows_you, followed_at=COALESCE(network.followed_at, excluded.followed_at),
                           source='import', updated_at=excluded.updated_at""",
                    (handle, 1 if handle in following else 0, 1 if handle in followers else 0,
                     first_seen, followed_at, now),
                )
        return len(following), len(followers)


    def sync_live_network(self, following_users: list[RawAuthor], follower_users: list[RawAuthor]) -> tuple[int, int]:
        """Replace relationship flags with the authoritative live X network snapshot.

        Historical rows/decisions remain local, but demo relationships are discarded once
        a real account is synced. For pre-existing follows where X does not expose the
        original follow date, Scout starts the grace clock at first live observation.
        """
        now = datetime.now(timezone.utc).isoformat()
        following_by_handle = {a.username.lower(): a for a in following_users if a.username}
        followers_by_handle = {a.username.lower(): a for a in follower_users if a.username}
        handles = set(following_by_handle) | set(followers_by_handle)
        with closing(self.connect()) as conn, conn:
            conn.execute("DELETE FROM network WHERE source='demo'")
            conn.execute("UPDATE network SET you_follow=0, follows_you=0, updated_at=?", (now,))
            for handle in handles:
                author = following_by_handle.get(handle) or followers_by_handle.get(handle)
                assert author is not None
                existing = conn.execute(
                    "SELECT first_seen_at, followed_at, protected, you_follow FROM network WHERE username=? COLLATE NOCASE",
                    (handle,),
                ).fetchone()
                first_seen = str(existing["first_seen_at"]) if existing else now
                was_following = bool(existing and existing["you_follow"])
                already_followed_at = str(existing["followed_at"] or "") if existing and was_following else ""
                you_follow = 1 if handle in following_by_handle else 0
                follows_you = 1 if handle in followers_by_handle else 0
                followed_at = already_followed_at or (now if you_follow else None)
                protected = int(existing["protected"]) if existing else 0
                conn.execute(
                    """INSERT INTO network(username, display_name, user_id, you_follow, follows_you,
                                             first_seen_at, followed_at, source, protected, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'x-live', ?, ?)
                       ON CONFLICT(username) DO UPDATE SET display_name=excluded.display_name,
                           user_id=excluded.user_id, you_follow=excluded.you_follow,
                           follows_you=excluded.follows_you,
                           followed_at=COALESCE(network.followed_at, excluded.followed_at),
                           source='x-live', protected=network.protected, updated_at=excluded.updated_at""",
                    (handle, author.name, author.user_id, you_follow, follows_you, first_seen, followed_at, protected, now),
                )
        return len(following_by_handle), len(followers_by_handle)

    def network_user_id(self, username: str) -> str:
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT user_id FROM network WHERE username=? COLLATE NOCASE", (username,)).fetchone()
        return str(row["user_id"] or "") if row else ""

    def following_handles(self) -> set[str]:
        with closing(self.connect()) as conn:
            rows = conn.execute("SELECT username FROM network WHERE you_follow=1").fetchall()
        return {str(r["username"]).lower() for r in rows}

    def relationship(self, username: str) -> str:
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT * FROM network WHERE username=? COLLATE NOCASE", (username,)).fetchone()
        if not row:
            return "NEW"
        if row["you_follow"] and row["follows_you"]:
            return "MUTUAL"
        if row["you_follow"] and row["protected"]:
            return "PROTECTED"
        if row["you_follow"]:
            return self._waiting_status(dict(row))
        if row["follows_you"]:
            return "FOLLOWS YOU"
        return "NONE"

    @staticmethod
    def _waiting_status(row: dict, grace_days: int = 7) -> str:
        if row.get("follows_you"):
            return "MUTUAL"
        followed = row.get("followed_at") or row.get("first_seen_at")
        try:
            dt = datetime.fromisoformat(str(followed))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 86400
        except Exception:
            age_days = 0
        return "REVIEW" if age_days >= grace_days else "GRACE"

    def follow_author(self, author: RawAuthor, source: str = "scout") -> None:
        now = datetime.now(timezone.utc).isoformat()
        with closing(self.connect()) as conn, conn:
            conn.execute(
                """INSERT INTO network(username, display_name, user_id, you_follow, follows_you,
                                       first_seen_at, followed_at, source, updated_at)
                   VALUES (?, ?, ?, 1, 0, ?, ?, ?, ?)
                   ON CONFLICT(username) DO UPDATE SET display_name=excluded.display_name,
                       user_id=excluded.user_id, you_follow=1,
                       followed_at=CASE WHEN network.you_follow=0 THEN excluded.followed_at
                                        ELSE COALESCE(network.followed_at, excluded.followed_at) END,
                       source=excluded.source, updated_at=excluded.updated_at""",
                (author.username.lower(), author.name, author.user_id, now, now, source, now),
            )


    def set_protected(self, username: str, protected: bool) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "UPDATE network SET protected=?, updated_at=? WHERE username=? COLLATE NOCASE",
                (1 if protected else 0, now, username),
            )

    def unfollow(self, username: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with closing(self.connect()) as conn, conn:
            conn.execute("UPDATE network SET you_follow=0, updated_at=? WHERE username=? COLLATE NOCASE", (now, username))

    def network_rows(self) -> list[dict]:
        with closing(self.connect()) as conn:
            rows = conn.execute("SELECT * FROM network ORDER BY username COLLATE NOCASE").fetchall()
        out: list[dict] = []
        for raw in rows:
            row = dict(raw)
            row["status"] = self.relationship(str(row["username"]))
            followed = row.get("followed_at")
            if followed:
                try:
                    dt = datetime.fromisoformat(str(followed))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    row["age_days"] = max(0, int((datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() // 86400))
                except Exception:
                    row["age_days"] = 0
            else:
                row["age_days"] = None
            out.append(row)
        status_order = {"REVIEW": 0, "GRACE": 1, "PROTECTED": 2, "MUTUAL": 3, "FOLLOWS YOU": 4, "NONE": 5}
        out.sort(key=lambda r: (status_order.get(r["status"], 9), str(r["username"])))
        return out

    def network_stats(self) -> dict[str, int]:
        rows = self.network_rows()
        return {
            "following": sum(1 for r in rows if r["you_follow"]),
            "followers": sum(1 for r in rows if r["follows_you"]),
            "mutual": sum(1 for r in rows if r["status"] == "MUTUAL"),
            "review": sum(1 for r in rows if r["status"] == "REVIEW"),
            "protected": sum(1 for r in rows if r["status"] == "PROTECTED"),
        }
