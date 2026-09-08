from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_x_datetime(value: str | None) -> datetime:
    if not value:
        return utc_now()
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(slots=True)
class RawAuthor:
    user_id: str
    username: str
    name: str
    description: str
    verified: bool
    verified_type: str
    followers_count: int
    following_count: int = 0
    post_count: int = 0
    location: str = ""
    created_at: str = ""

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "RawAuthor":
        metrics = data.get("public_metrics") or {}
        return cls(
            user_id=str(data.get("id", "")),
            username=str(data.get("username", "")),
            name=str(data.get("name", "")),
            description=str(data.get("description", "") or ""),
            verified=bool(data.get("verified", False)),
            verified_type=str(data.get("verified_type", "none") or "none").lower(),
            followers_count=int(metrics.get("followers_count", 0) or 0),
            following_count=int(metrics.get("following_count", 0) or 0),
            post_count=int(metrics.get("tweet_count", metrics.get("post_count", 0)) or 0),
            location=str(data.get("location", "") or ""),
            created_at=str(data.get("created_at", "") or ""),
        )


@dataclass(slots=True)
class RawPost:
    post_id: str
    author_id: str
    text: str
    created_at: datetime
    reply_count: int
    like_count: int
    repost_count: int
    quote_count: int
    impression_count: int = 0
    conversation_id: str = ""
    lang: str = "en"

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "RawPost":
        metrics = data.get("public_metrics") or {}
        return cls(
            post_id=str(data.get("id", "")),
            author_id=str(data.get("author_id", "")),
            text=str(data.get("text", "") or ""),
            created_at=parse_x_datetime(data.get("created_at")),
            reply_count=int(metrics.get("reply_count", 0) or 0),
            like_count=int(metrics.get("like_count", 0) or 0),
            repost_count=int(metrics.get("retweet_count", metrics.get("repost_count", 0)) or 0),
            quote_count=int(metrics.get("quote_count", 0) or 0),
            impression_count=int(metrics.get("impression_count", 0) or 0),
            conversation_id=str(data.get("conversation_id", "") or ""),
            lang=str(data.get("lang", "en") or "en"),
        )


@dataclass(slots=True)
class PersonCandidate:
    author: RawAuthor
    latest_post_at: datetime
    matched_posts: list[RawPost] = field(default_factory=list)
    score: float = 0.0
    reason: str = ""
    decision: str = "unreviewed"

    @property
    def profile_url(self) -> str:
        return f"https://x.com/{self.author.username}"


@dataclass(slots=True)
class ConversationCandidate:
    post: RawPost
    author: RawAuthor
    score: float = 0.0
    reason: str = ""
    decision: str = "unreviewed"

    @property
    def post_url(self) -> str:
        return f"https://x.com/{self.author.username}/status/{self.post.post_id}"
