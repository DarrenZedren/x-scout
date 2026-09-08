import tempfile
import unittest
from pathlib import Path

from x_scout.database import ScoutDatabase


class DatabaseTests(unittest.TestCase):
    def test_decision_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            db = ScoutDatabase(Path(td) / "test.sqlite3")
            db.set_decision("person", "abc", "keep")
            self.assertEqual(db.get_decisions("person")["abc"], "keep")
            db.set_decision("person", "abc", "never_show")
            self.assertEqual(db.get_decisions("person")["abc"], "never_show")

    def test_not_for_me_reason_feedback_count(self):
        with tempfile.TemporaryDirectory() as td:
            db = ScoutDatabase(Path(td) / "test.sqlite3")
            db.set_decision("person", "a", "not_for_me", "spammy")
            db.set_decision("person", "b", "not_for_me", "spammy")
            db.set_decision("person", "c", "not_for_me", "too_salesy")
            self.assertEqual(db.feedback_counts(), {"spammy": 2, "too_salesy": 1})

    def test_seen_is_per_profile_and_resettable(self):
        with tempfile.TemporaryDirectory() as td:
            db = ScoutDatabase(Path(td) / "test.sqlite3")
            db.mark_seen("AI Builders", "person", ["1", "2"])
            db.mark_seen("Classic Cars", "person", ["1"])
            self.assertEqual(db.seen_ids("AI Builders", "person"), {"1", "2"})
            self.assertEqual(db.seen_ids("Classic Cars", "person"), {"1"})
            self.assertEqual(db.reset_seen("AI Builders"), 2)
            self.assertEqual(db.seen_ids("AI Builders", "person"), set())
            self.assertEqual(db.seen_ids("Classic Cars", "person"), {"1"})


if __name__ == "__main__":
    unittest.main()
