from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable

from .database import ScoutDatabase
from .models import ConversationCandidate, PersonCandidate, RawPost
from .scoring import age_hours, score_conversation, score_person

Progress = Callable[[int, str], None]


class ScoutEngine:
    def __init__(self, provider, config: dict, db: ScoutDatabase):
        self.provider = provider
        self.config = config
        self.db = db

    def scan(self, mode: str, progress: Progress | None = None) -> tuple[list[PersonCandidate], list[ConversationCandidate]]:
        scan_id = self.db.start_scan(mode)
        try:
            profile_name = str(self.config.get("profile_name") or "default")
            if progress:
                progress(3, "Mission profile locked. Launching fresh scout sweep.")

            # No candidate cache: every START asks the provider for a fresh 24h discovery batch.
            batch = self.provider.discover(progress)

            max_hours = float(self.config["activity"]["maximum_hours"])
            current_posts = [p for p in batch.posts if age_hours(p.created_at) <= max_hours]
            discarded_old = len(batch.posts) - len(current_posts)
            if progress:
                progress(62, f"24h gate: {len(current_posts)} current signals; {discarded_old} stale signals discarded")

            posts_by_author: dict[str, list[RawPost]] = defaultdict(list)
            for p in current_posts:
                if p.author_id in batch.authors:
                    posts_by_author[p.author_id].append(p)

            person_decisions = self.db.get_decisions("person")
            conversation_decisions = self.db.get_decisions("conversation")
            rejected_people = {
                uid for uid, decision in person_decisions.items()
                if decision in {"not_for_me", "never_show"}
            }
            already_following = self.db.following_handles()
            seen_people = self.db.seen_ids(profile_name, "person")
            seen_conversations = self.db.seen_ids(profile_name, "conversation")

            people_floor = float((self.config.get("quality_floor") or {}).get("people", 0))
            conversations_floor = float((self.config.get("quality_floor") or {}).get("conversations", 0))

            people: list[PersonCandidate] = []
            hidden_following = 0
            hidden_seen_people = 0
            hidden_rejected = 0
            below_floor_people = 0
            for uid, posts in posts_by_author.items():
                author = batch.authors[uid]
                if uid in rejected_people:
                    hidden_rejected += 1
                    continue
                if uid in seen_people:
                    hidden_seen_people += 1
                    continue
                if author.username.lower() in already_following:
                    hidden_following += 1
                    continue
                latest = max((p.created_at for p in posts), default=datetime.now(timezone.utc))
                c = PersonCandidate(author=author, latest_post_at=latest, matched_posts=posts)
                c.decision = person_decisions.get(uid, "unreviewed")
                score_person(c, self.config)
                if c.score < 0:
                    continue
                if c.score < people_floor:
                    below_floor_people += 1
                    continue
                people.append(c)

            if progress:
                progress(
                    72,
                    f"People gate: {len(people)} above quality {people_floor:g}; "
                    f"{hidden_seen_people} already shown; {hidden_following} already followed; "
                    f"{below_floor_people} below floor",
                )

            conversations: list[ConversationCandidate] = []
            hidden_seen_conversations = 0
            below_floor_conversations = 0
            for post in current_posts:
                author = batch.authors.get(post.author_id)
                if not author or post.author_id in rejected_people:
                    continue
                if post.post_id in seen_conversations:
                    hidden_seen_conversations += 1
                    continue
                c = ConversationCandidate(post=post, author=author)
                c.decision = conversation_decisions.get(post.post_id, "unreviewed")
                score_conversation(c, self.config)
                if c.score < 0 or c.decision == "never_show":
                    continue
                if c.score < conversations_floor:
                    below_floor_conversations += 1
                    continue
                conversations.append(c)

            people.sort(key=lambda c: (-c.score, -c.latest_post_at.timestamp(), c.author.username.lower()))
            conversations.sort(key=lambda c: (-c.score, -c.post.created_at.timestamp(), c.author.username.lower()))

            # 25 is a ceiling, never a quota. If only 11 clear the quality floor, Scout returns 11.
            people = people[: int(self.config["results"]["people"])]
            conversations = conversations[: int(self.config["results"]["conversations"])]

            if progress:
                progress(
                    86,
                    f"Shortlist resolved: {len(people)} people + {len(conversations)} conversations; "
                    f"{hidden_seen_conversations} prior openings skipped",
                )

            self.db.save_people(people)
            self.db.save_conversations(conversations)
            # Only items actually presented are marked seen. Unshown candidates are not cached or consumed.
            self.db.mark_seen(profile_name, "person", [c.author.user_id for c in people])
            self.db.mark_seen(profile_name, "conversation", [c.post.post_id for c in conversations])
            self.db.finish_scan(scan_id, len(people), len(conversations), "complete")

            if progress:
                progress(100, f"Mission complete: {len(people)} people + {len(conversations)} conversations")
            return people, conversations
        except Exception:
            self.db.finish_scan(scan_id, 0, 0, "failed")
            raise
