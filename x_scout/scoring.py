from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Iterable

from .models import ConversationCandidate, PersonCandidate


SALESY_TERMS = [
    "book a call", "buy now", "limited offer", "discount", "sale", "subscribe", "course",
    "coaching", "client slots", "dm me", "link in bio", "newsletter", "customers", "leads",
]
SPAMMY_TERMS = [
    "guaranteed", "free money", "giveaway", "follow4follow", "follow back", "airdrop", "dm for details",
    "opportunity", "earn daily", "passive income", "signal group",
]
RAGE_TERMS = [
    "outrage", "breaking", "disgrace", "traitor", "destroyed", "exposed", "woke", "culture war",
]
CRYPTO_TERMS = ["crypto", "bitcoin", "btc", "token", "airdrop", "memecoin", "forex", "binance"]
ENGAGEMENT_FARM_TERMS = [
    "agree?", "thoughts?", "what do you think", "drop your", "comment below", "like and repost",
    "repost if", "follow me", "who else", "gm everyone",
]
GENERIC_HYPE_TERMS = [
    "game changer", "future is here", "this changes everything", "mind blowing", "mind-blowing",
    "insane", "10x", "unlock your", "you won't believe",
]


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def term_hits(text: str, terms: Iterable[str]) -> list[str]:
    haystack = _normalise(text)
    hits: list[str] = []
    for term in terms:
        t = _normalise(term)
        if t and t in haystack:
            hits.append(term)
    return hits


def blocked_hits(text: str, config: dict) -> list[str]:
    return term_hits(text, config["blocked_terms"])


def relevance_hits(text: str, config: dict) -> list[str]:
    return term_hits(text, config["wanted_terms"])


def age_hours(dt: datetime, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (now - dt.astimezone(timezone.utc)).total_seconds() / 3600.0)


def _feedback_penalty(text: str, config: dict, *, is_business: bool, builder_hits: list[str]) -> tuple[float, list[str]]:
    """Apply small, transparent taste penalties learned from NOT FOR ME reason tags.

    Counts are intentionally capped. Feedback should nudge the radar, not silently rewrite the mission profile.
    """
    feedback = config.get("taste_feedback") or {}
    penalty = 0.0
    notes: list[str] = []

    def add(reason: str, terms: list[str], label: str, cap: float = 12.0) -> None:
        nonlocal penalty
        count = int(feedback.get(reason, 0) or 0)
        if count <= 0:
            return
        if term_hits(text, terms):
            p = min(cap, 2.0 + count * 1.5)
            penalty += p
            notes.append(label)

    add("spammy", SPAMMY_TERMS, "spam pattern")
    add("too_salesy", SALESY_TERMS, "salesy pattern")
    add("ragebait", RAGE_TERMS, "ragebait pattern")
    add("crypto_rubbish", CRYPTO_TERMS, "crypto-heavy pattern")
    add("engagement_farmer", ENGAGEMENT_FARM_TERMS, "engagement-farm pattern")

    low_quality_count = int(feedback.get("low_quality", 0) or 0)
    if low_quality_count > 0 and (term_hits(text, GENERIC_HYPE_TERMS) or len(_normalise(text)) < 100):
        p = min(10.0, 2.0 + low_quality_count * 1.25)
        penalty += p
        notes.append("low-information pattern")

    not_builder_count = int(feedback.get("not_builder", 0) or 0)
    if not_builder_count > 0 and not builder_hits:
        p = min(12.0, 3.0 + not_builder_count * 1.5)
        penalty += p
        notes.append("weak builder evidence")

    corp_count = int(feedback.get("too_corporate", 0) or 0)
    if corp_count > 0 and is_business:
        p = min(10.0, 2.0 + corp_count * 1.25)
        penalty += p
        notes.append("corporate pattern")

    return penalty, notes


def hard_person_reject(candidate: PersonCandidate, config: dict) -> str | None:
    a = candidate.author
    allowed = {x.lower() for x in config["verification"]["allowed_types"]}
    if not a.verified or a.verified_type.lower() not in allowed:
        return "verification type not allowed"

    fmin = int(config["followers"]["minimum"])
    fmax = int(config["followers"]["maximum"])
    if a.followers_count < fmin or a.followers_count > fmax:
        return "outside follower range"

    combined = " ".join([a.description] + [p.text for p in candidate.matched_posts])
    bad = blocked_hits(combined, config)
    if bad:
        return f"blocked topic: {bad[0]}"

    if not relevance_hits(combined, config):
        return "account is not sufficiently relevant"

    max_hours = float(config["activity"]["maximum_hours"])
    if age_hours(candidate.latest_post_at) > max_hours:
        return "no qualifying post in the last 24 hours"
    return None


def score_person(candidate: PersonCandidate, config: dict) -> PersonCandidate:
    reject = hard_person_reject(candidate, config)
    if reject:
        candidate.score = -1
        candidate.reason = reject
        return candidate

    a = candidate.author
    combined = " ".join([a.description] + [p.text for p in candidate.matched_posts])
    rel = relevance_hits(combined, config)
    builder = term_hits(combined, config.get("activity_terms", config.get("builder_terms", [])))

    distinct_rel = len(set(x.lower() for x in rel))
    distinct_builder = len(set(x.lower() for x in builder))
    relevant_posts = sum(1 for p in candidate.matched_posts if relevance_hits(p.text, config))

    # 35 relevance + 20 builder evidence + 15 repeated activity + 20 recency + 10 accessibility = 100.
    score = 0.0
    score += min(35.0, 18.0 + max(0, distinct_rel - 1) * 6.0)
    score += min(20.0, (10.0 if distinct_builder else 0.0) + max(0, distinct_builder - 1) * 3.0)
    score += min(15.0, relevant_posts * 5.0)

    hours = age_hours(candidate.latest_post_at)
    highest = float(config["activity"].get("highest_boost_hours", 6))
    strong = float(config["activity"].get("strong_boost_hours", 12))
    maximum = float(config["activity"].get("maximum_hours", 24))
    if hours <= highest:
        score += 20
    elif hours <= strong:
        score += 16
    elif hours <= maximum:
        score += 10

    preferred = int(config["followers"]["preferred_maximum"])
    score += 10 if 100 <= a.followers_count <= preferred else 5

    penalty, feedback_notes = _feedback_penalty(
        combined, config, is_business=a.verified_type == "business", builder_hits=builder
    )
    score -= penalty
    candidate.score = max(0.0, min(100.0, round(score, 1)))

    topics = ", ".join(dict.fromkeys(rel[:4])) if rel else "relevant build activity"
    freshness = "active <6h" if hours <= highest else "active <12h" if hours <= strong else "active <24h"
    evidence = f"{relevant_posts} relevant post{'s' if relevant_posts != 1 else ''}"
    penalty_note = f"; taste penalty: {', '.join(feedback_notes)}" if feedback_notes else ""
    candidate.reason = f"{topics}; {evidence}; {freshness}; {a.followers_count:,} followers{penalty_note}"
    return candidate


def hard_conversation_reject(candidate: ConversationCandidate, config: dict) -> str | None:
    a = candidate.author
    allowed = {x.lower() for x in config["verification"]["allowed_types"]}
    if not a.verified or a.verified_type.lower() not in allowed:
        return "verification type not allowed"

    fmin = int(config["followers"]["minimum"])
    fmax = int(config["followers"]["maximum"])
    if a.followers_count < fmin or a.followers_count > fmax:
        return "outside follower range"

    bad = blocked_hits(f"{a.description} {candidate.post.text}", config)
    if bad:
        return f"blocked topic: {bad[0]}"

    if not relevance_hits(candidate.post.text, config):
        return "post is not sufficiently relevant"

    if age_hours(candidate.post.created_at) > float(config["activity"]["maximum_hours"]):
        return "post older than 24 hours"
    return None


def score_conversation(candidate: ConversationCandidate, config: dict) -> ConversationCandidate:
    reject = hard_conversation_reject(candidate, config)
    if reject:
        candidate.score = -1
        candidate.reason = reject
        return candidate

    p = candidate.post
    a = candidate.author
    rel = relevance_hits(p.text, config)
    builder = term_hits(p.text, config.get("activity_terms", config.get("builder_terms", [])))
    distinct_rel = len(set(x.lower() for x in rel))
    distinct_builder = len(set(x.lower() for x in builder))

    # 35 topic + 20 substance + 25 freshness + 15 reply accessibility + 5 creator accessibility = 100.
    score = min(35.0, 20.0 + max(0, distinct_rel - 1) * 5.0)

    substance = 0.0
    if distinct_builder:
        substance += min(14.0, 9.0 + max(0, distinct_builder - 1) * 2.5)
    if len(p.text.strip()) >= 90:
        substance += 4.0
    if "?" in p.text:
        substance += 3.0
    score += min(20.0, substance)

    hours = age_hours(p.created_at)
    if hours <= 1:
        score += 25
    elif hours <= 3:
        score += 23
    elif hours <= 6:
        score += 21
    elif hours <= 12:
        score += 17
    else:
        score += 11

    if p.reply_count <= 20:
        score += 15
    elif p.reply_count <= 50:
        score += 11
    elif p.reply_count <= 150:
        score += 6
    elif p.reply_count <= 500:
        score += 1
    else:
        score -= 8

    preferred = int(config["followers"]["preferred_maximum"])
    score += 5 if a.followers_count <= preferred else 2

    combined = f"{a.description} {p.text}"
    penalty, feedback_notes = _feedback_penalty(
        combined, config, is_business=a.verified_type == "business", builder_hits=builder
    )
    score -= penalty
    candidate.score = max(0.0, min(100.0, round(score, 1)))

    topics = ", ".join(dict.fromkeys(rel[:3])) if rel else "relevant topic"
    if p.reply_count <= 20:
        crowd = "small conversation"
    elif p.reply_count <= 50:
        crowd = "approachable conversation"
    elif p.reply_count <= 150:
        crowd = "moderate conversation"
    else:
        crowd = "busy conversation"
    penalty_note = f"; taste penalty: {', '.join(feedback_notes)}" if feedback_notes else ""
    candidate.reason = f"{topics}; {crowd}; {p.reply_count} replies; {hours:.1f}h old{penalty_note}"
    return candidate
