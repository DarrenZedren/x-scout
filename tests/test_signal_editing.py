import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from x_scout.bridge import ScoutBridge
from x_scout.database import ScoutDatabase
from x_scout.topic_profiles import query_groups_for_terms


class SignalEditingTests(unittest.TestCase):
    def test_query_groups_are_rebuilt_from_remaining_core_signals(self):
        groups = query_groups_for_terms(["AI coding", "vibe coding", "Claude"])
        flat = [term for group in groups for term in group]
        self.assertIn("AI coding", flat)
        self.assertIn("Claude", flat)
        self.assertNotIn("Python", flat)

    def test_save_profile_does_not_keep_removed_term_in_stale_query_groups(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "scout.sqlite3"
            temp_db = ScoutDatabase(path=db_path)
            with patch("x_scout.bridge.ScoutDatabase", return_value=temp_db):
                bridge = ScoutBridge()
                payload = bridge.analyse_topics("AI, vibe coding")
                payload["name"] = "Pruned AI"
                payload["wantedTerms"] = [t for t in payload["wantedTerms"] if t.lower() != "python"]
                # Deliberately simulate stale client data containing Python.
                payload["queryGroups"] = [["AI coding", "Python", "Claude"]]
                state = bridge.save_profile(payload)
                active = state["activeProfile"]
                flat = [term for group in active["queryGroups"] for term in group]
                self.assertNotIn("Python", flat)
                self.assertNotIn("python", [x.lower() for x in flat])


if __name__ == "__main__":
    unittest.main()
