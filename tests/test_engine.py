import json
import tempfile
import unittest
from pathlib import Path

from x_scout.database import ScoutDatabase
from x_scout.engine import ScoutEngine
from x_scout.providers import MockProvider
from x_scout.scoring import age_hours


ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


def mission_config(db: ScoutDatabase, **overrides):
    cfg = json.loads(json.dumps(CONFIG))
    p = db.active_profile()
    cfg.update(
        profile_name=p.name,
        wanted_terms=p.wanted_terms,
        blocked_terms=p.blocked_terms,
        activity_terms=p.activity_terms,
        query_groups=p.query_groups,
        taste_feedback=db.feedback_counts(),
    )
    for k, v in overrides.items():
        cfg[k] = v
    return cfg


class EngineTests(unittest.TestCase):
    def test_never_show_person_removes_their_conversations(self):
        with tempfile.TemporaryDirectory() as td:
            db = ScoutDatabase(Path(td) / "test.sqlite3")
            cfg = mission_config(db)
            people, conversations = ScoutEngine(MockProvider(cfg, seed=123), cfg, db).scan("test")
            self.assertTrue(people)
            blocked = people[0]
            db.set_decision("person", blocked.author.user_id, "never_show")
            db.reset_seen(cfg["profile_name"])

            people2, conversations2 = ScoutEngine(MockProvider(cfg, seed=123), cfg, db).scan("test")
            self.assertNotIn(blocked.author.user_id, {p.author.user_id for p in people2})
            self.assertNotIn(blocked.author.user_id, {c.author.user_id for c in conversations2})

    def test_not_for_me_person_is_suppressed_and_teaches_reason(self):
        with tempfile.TemporaryDirectory() as td:
            db = ScoutDatabase(Path(td) / "test.sqlite3")
            cfg = mission_config(db)
            people, _ = ScoutEngine(MockProvider(cfg, seed=222), cfg, db).scan("test")
            rejected = people[0]
            db.set_decision("person", rejected.author.user_id, "not_for_me", "too_salesy")
            db.reset_seen(cfg["profile_name"])
            cfg["taste_feedback"] = db.feedback_counts()
            people2, conversations2 = ScoutEngine(MockProvider(cfg, seed=222), cfg, db).scan("test")
            self.assertNotIn(rejected.author.user_id, {p.author.user_id for p in people2})
            self.assertNotIn(rejected.author.user_id, {c.author.user_id for c in conversations2})
            self.assertEqual(db.feedback_counts().get("too_salesy"), 1)

    def test_scan_returns_only_last_24h(self):
        with tempfile.TemporaryDirectory() as td:
            db = ScoutDatabase(Path(td) / "test.sqlite3")
            cfg = mission_config(db)
            people, conversations = ScoutEngine(MockProvider(cfg, seed=777), cfg, db).scan("test")
            self.assertTrue(people)
            self.assertTrue(conversations)
            self.assertTrue(all(age_hours(p.latest_post_at) <= 24 for p in people))
            self.assertTrue(all(age_hours(c.post.created_at) <= 24 for c in conversations))

    def test_repeated_start_returns_unseen_results(self):
        with tempfile.TemporaryDirectory() as td:
            db = ScoutDatabase(Path(td) / "test.sqlite3")
            cfg = mission_config(db)
            first_p, first_c = ScoutEngine(MockProvider(cfg, seed=999), cfg, db).scan("test")
            second_p, second_c = ScoutEngine(MockProvider(cfg, seed=999), cfg, db).scan("test")
            self.assertTrue(first_p and second_p and first_c and second_c)
            self.assertTrue({p.author.user_id for p in first_p}.isdisjoint({p.author.user_id for p in second_p}))
            self.assertTrue({c.post.post_id for c in first_c}.isdisjoint({c.post.post_id for c in second_c}))

    def test_result_limit_is_ceiling_not_quota(self):
        with tempfile.TemporaryDirectory() as td:
            db = ScoutDatabase(Path(td) / "test.sqlite3")
            cfg = mission_config(db)
            cfg["quality_floor"] = {"people": 94, "conversations": 82}
            people, conversations = ScoutEngine(MockProvider(cfg, seed=123), cfg, db).scan("test")
            self.assertLess(len(people), 25)
            self.assertLess(len(conversations), 25)
            self.assertTrue(all(p.score >= 94 for p in people))
            self.assertTrue(all(c.score >= 82 for c in conversations))


if __name__ == "__main__":
    unittest.main()
