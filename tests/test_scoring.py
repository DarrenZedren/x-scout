import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from x_scout.models import ConversationCandidate, PersonCandidate, RawAuthor, RawPost
from x_scout.scoring import score_conversation, score_person


ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


class ScoringTests(unittest.TestCase):
    def author(self, **overrides):
        data = dict(
            user_id="1", username="builder", name="Builder", description="Building AI agents with Python and APIs",
            verified=True, verified_type="blue", followers_count=1200,
        )
        data.update(overrides)
        return RawAuthor(**data)

    def post(self, **overrides):
        data = dict(
            post_id="10", author_id="1", text="Built an AI agent orchestration tool with Python today",
            created_at=datetime.now(timezone.utc) - timedelta(hours=2), reply_count=8, like_count=20,
            repost_count=2, quote_count=0,
        )
        data.update(overrides)
        return RawPost(**data)

    def test_good_person_scores(self):
        p = self.post()
        c = PersonCandidate(self.author(), p.created_at, [p])
        score_person(c, CONFIG)
        self.assertGreaterEqual(c.score, 60)

    def test_government_rejected(self):
        p = self.post()
        c = PersonCandidate(self.author(verified_type="government"), p.created_at, [p])
        score_person(c, CONFIG)
        self.assertEqual(c.score, -1)

    def test_politics_rejected(self):
        p = self.post(text="AI agents are cool but election politics outrage dominates today")
        c = PersonCandidate(self.author(), p.created_at, [p])
        score_person(c, CONFIG)
        self.assertEqual(c.score, -1)

    def test_small_recent_conversation_scores_well(self):
        c = ConversationCandidate(self.post(), self.author())
        score_conversation(c, CONFIG)
        self.assertGreaterEqual(c.score, 60)

    def test_person_older_than_24h_rejected(self):
        p = self.post(created_at=datetime.now(timezone.utc) - timedelta(hours=24, minutes=5))
        c = PersonCandidate(self.author(), p.created_at, [p])
        score_person(c, CONFIG)
        self.assertEqual(c.score, -1)

    def test_conversation_older_than_24h_rejected(self):
        p = self.post(created_at=datetime.now(timezone.utc) - timedelta(hours=25))
        c = ConversationCandidate(p, self.author())
        score_conversation(c, CONFIG)
        self.assertEqual(c.score, -1)

    def test_person_inside_12h_scores_above_same_person_at_20h(self):
        fresh = self.post(created_at=datetime.now(timezone.utc) - timedelta(hours=8))
        older = self.post(post_id="11", created_at=datetime.now(timezone.utc) - timedelta(hours=20))
        c_fresh = PersonCandidate(self.author(), fresh.created_at, [fresh])
        c_older = PersonCandidate(self.author(), older.created_at, [older])
        score_person(c_fresh, CONFIG)
        score_person(c_older, CONFIG)
        self.assertGreater(c_fresh.score, c_older.score)


if __name__ == "__main__":
    unittest.main()
