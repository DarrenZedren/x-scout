from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import RawAuthor, RawPost

Progress = Callable[[int, str], None]


class ProviderError(RuntimeError):
    pass


@dataclass(slots=True)
class DiscoveryBatch:
    posts: list[RawPost]
    authors: dict[str, RawAuthor]


class XApiProvider:
    """X recent-search provider using a local OAuth user access token."""

    ENDPOINT = "https://api.x.com/2/tweets/search/recent"

    def __init__(self, access_token: str, config: dict):
        if not access_token:
            raise ValueError("X access token is required")
        self.access_token = access_token
        self.config = config

    def _query_string(self, terms: Iterable[str]) -> str:
        def q(term: str) -> str:
            term = term.strip()
            return f'"{term}"' if " " in term else term
        core = " OR ".join(q(t) for t in terms)
        return f"({core}) is:verified lang:en -is:retweet -is:reply"

    def discover(self, progress: Progress | None = None) -> DiscoveryBatch:
        all_posts: dict[str, RawPost] = {}
        authors: dict[str, RawAuthor] = {}
        groups = self.config["query_groups"]
        max_results = max(10, min(100, int(self.config["api"]["max_results_per_query"])))
        max_pages = max(1, int(self.config["api"].get("max_pages_per_query", 1)))

        for idx, terms in enumerate(groups, start=1):
            query = self._query_string(terms)
            if len(query) > 512:
                raise ProviderError(f"Configured X query exceeds 512 characters: {query[:80]}...")

            start_time = (datetime.now(timezone.utc) - timedelta(hours=float(self.config["activity"]["maximum_hours"]))).isoformat(timespec="seconds").replace("+00:00", "Z")
            next_token = None
            for page in range(1, max_pages + 1):
                params = {
                    "query": query,
                    "start_time": start_time,
                    "max_results": max_results,
                    "expansions": "author_id",
                    "tweet.fields": "id,text,author_id,created_at,public_metrics,lang,conversation_id",
                    "user.fields": "id,name,username,description,verified,verified_type,public_metrics,created_at,location",
                }
                if next_token:
                    params["next_token"] = next_token
                url = f"{self.ENDPOINT}?{urlencode(params)}"
                req = Request(url, headers={"Authorization": f"Bearer {self.access_token}", "User-Agent": "X-Scout/0.4.0", "Accept": "application/json"}, method="GET")
                if progress:
                    total_slots = max(1, len(groups) * max_pages)
                    done_slots = (idx - 1) * max_pages + (page - 1)
                    pct = 10 + int(done_slots / total_slots * 45)
                    progress(pct, f"LIVE X // search vector {idx}/{len(groups)} // page {page}/{max_pages}")
                try:
                    with urlopen(req, timeout=30) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                except HTTPError as exc:
                    body = exc.read().decode("utf-8", errors="replace")
                    raise ProviderError(f"X API HTTP {exc.code}: {body[:500]}") from exc
                except URLError as exc:
                    raise ProviderError(f"Could not reach X API: {exc.reason}") from exc

                for raw_user in (payload.get("includes") or {}).get("users", []):
                    author = RawAuthor.from_api(raw_user)
                    if author.user_id:
                        authors[author.user_id] = author
                for raw_post in payload.get("data", []) or []:
                    post = RawPost.from_api(raw_post)
                    if post.post_id and post.author_id:
                        all_posts[post.post_id] = post

                next_token = str((payload.get("meta") or {}).get("next_token") or "")
                if not next_token:
                    break

        if progress:
            progress(58, f"LIVE X // {len(all_posts)} unique posts from {len(authors)} verified authors")
        return DiscoveryBatch(list(all_posts.values()), authors)



class MockProvider:
    """Profile-aware synthetic provider used to test the complete mission-control workflow offline."""

    HUMAN_FIRST = [
        "Maya", "Theo", "Priya", "Owen", "Nina", "Marcus", "Avery", "Lena", "Sam", "Riley", "Noah", "Zoe",
        "Eli", "Tessa", "Jonah", "Mila", "Arjun", "Sophie", "Caleb", "Keira", "Ben", "Iris", "Miles", "Anika",
        "Leo", "Freya", "Dylan", "Nora", "Jules", "Harper", "Finn", "Mina", "Alex", "Eva", "Oscar", "Jade",
        "Tom", "Sara", "Max", "Clara",
    ]
    HUMAN_LAST = ["Vale", "Chen", "Nair", "Grant", "Park", "Bell", "Ortega", "Walsh", "Rao", "Lee", "Quinn", "Brooks", "Stone", "Patel", "Reed", "Morgan", "Kim", "Lewis", "Singh", "Hart"]
    HANDLE_SUFFIXES = ["works", "lab", "fieldnotes", "maker", "bench", "project", "studio", "journal", "craft", "signal", "workshop", "daily", "notes", "builds", "tests"]
    COMPANY_NAMES = ["Signal Works", "Field Notes Lab", "Northline Studio", "Workshop Zero", "Useful Projects", "Orbit Bench", "Small Systems Co", "Practical Lab", "Motive Works", "Open Bench", "Brightline Studio", "Project Foundry"]
    GOOD_LINES = [
        "Working hands-on with {topic} today. The practical details are where it gets interesting.",
        "A small {topic} experiment from this morning. Tested it twice before posting the result.",
        "Today's project is {topic}. Sharing the useful part rather than pretending the first attempt worked.",
        "Deep dive into {topic} this afternoon. Found one approach that is much simpler than the usual advice.",
        "Current work: {topic}. Still rough, but there is enough here to be worth discussing.",
        "Spent a few hours on {topic}; learned more from the failed version than the polished one.",
        "Quick field note on {topic}: a small change made a surprisingly large difference.",
        "Making progress on {topic}. Posting the process because the messy middle is usually the useful bit.",
    ]
    BAD_LINES = [
        "Breaking political outrage election culture war news everyone must be furious.",
        "Immigration border crisis outrage thread. Politics all day.",
        "Religion debate ragebait breaking news and more outrage.",
        "FREE giveaway follow4follow guaranteed profit DM me for details.",
        "True crime murder breaking news outrage thread.",
    ]

    def __init__(self, config: dict, seed: int = 7319):
        self.config = config
        # Rotates every ten minutes for fresh-looking demos while remaining stable during one test session.
        self.random = random.Random(seed + int(time.time() // 600))

    def discover(self, progress: Progress | None = None) -> DiscoveryBatch:
        r = self.random
        now = datetime.now(timezone.utc)
        authors: dict[str, RawAuthor] = {}
        posts: list[RawPost] = []
        topics = [t for t in self.config.get("wanted_terms", []) if 2 <= len(t) <= 40][:20] or ["interesting projects"]

        if progress:
            progress(8, f"DEMO // synthesising current signals for {self.config.get('profile_name', 'active mission')}")
        time.sleep(0.25)

        account_count = 240
        for i in range(account_count):
            uid = f"demo-user-{i:03d}"
            verified_type = r.choices(["blue", "business", "government", "none"], weights=[72, 14, 4, 10], k=1)[0]
            verified = verified_type != "none"
            followers = int(10 ** r.uniform(1.8, 5.05))
            topic = topics[(i * 3 + r.randrange(len(topics))) % len(topics)]
            company = verified_type == "business"
            if company:
                base = self.COMPANY_NAMES[i % len(self.COMPANY_NAMES)]
                name = base
                username = "".join(ch for ch in base.lower() if ch.isalnum()) + f"_demo{i+1:02d}"
                description = f"Independent company working around {topic}. We share current projects and practical notes."
            else:
                first = self.HUMAN_FIRST[i % len(self.HUMAN_FIRST)]
                last = self.HUMAN_LAST[(i * 7) % len(self.HUMAN_LAST)]
                suffix = self.HANDLE_SUFFIXES[(i * 11) % len(self.HANDLE_SUFFIXES)]
                name = f"{first} {last}"
                username = f"{first.lower()}_{suffix}_demo{i+1:02d}"
                description = f"Independent creator. Current interests: {topic}, practical projects and sharing what works."
            if i % 14 == 0:
                description += " Politics and election commentary."

            authors[uid] = RawAuthor(
                user_id=uid, username=username, name=name, description=description, verified=verified,
                verified_type=verified_type, followers_count=followers, following_count=r.randint(50, 5000),
                post_count=r.randint(80, 20000), location=r.choice(["Australia", "United Kingdom", "Canada", "United States", "New Zealand", ""]),
            )

            for j in range(r.randint(1, 4)):
                age = r.uniform(0.08, 38)
                created = now - timedelta(hours=age)
                post_topic = topic if r.random() < 0.7 else r.choice(topics)
                text = r.choice(self.BAD_LINES) if (i % 13 == 0 and j == 0) else r.choice(self.GOOD_LINES).format(topic=post_topic)
                posts.append(RawPost(
                    post_id=f"demo-post-{i:03d}-{j:02d}", author_id=uid, text=text, created_at=created,
                    reply_count=r.choices([r.randint(0, 18), r.randint(19, 80), r.randint(151, 900)], [67, 27, 6])[0],
                    like_count=r.randint(0, 900), repost_count=r.randint(0, 120), quote_count=r.randint(0, 35),
                    impression_count=r.randint(50, 60000), conversation_id=f"demo-post-{i:03d}-{j:02d}", lang="en",
                ))
            if progress and i in {50, 120, 200}:
                progress(15 + int(i / account_count * 35), f"DEMO // radar sweep {i+1}/{account_count} candidate accounts")
                time.sleep(0.18)

        if progress:
            progress(56, f"DEMO // {len(posts)} signals from {len(authors)} authors; applying mission filters")
        return DiscoveryBatch(posts, authors)
