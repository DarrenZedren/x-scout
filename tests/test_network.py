import tempfile
import unittest
from pathlib import Path
from x_scout.database import ScoutDatabase

class NetworkTests(unittest.TestCase):
    def test_import_and_relationship(self):
        with tempfile.TemporaryDirectory() as td:
            db = ScoutDatabase(Path(td)/"db.sqlite3")
            db.import_network_lists("@alpha @mutual", "@mutual @fan")
            self.assertIn(db.relationship("alpha"), {"GRACE", "REVIEW"})
            self.assertEqual(db.relationship("mutual"), "MUTUAL")
            self.assertEqual(db.relationship("fan"), "FOLLOWS YOU")
            self.assertIn("alpha", db.following_handles())

    def test_protected_follow_is_not_reviewed(self):
        with tempfile.TemporaryDirectory() as td:
            db = ScoutDatabase(Path(td)/"db.sqlite3")
            db.import_network_lists("@elonexample", "")
            db.set_protected("elonexample", True)
            self.assertEqual(db.relationship("elonexample"), "PROTECTED")



class LiveNetworkSyncTests(unittest.TestCase):
    def test_live_sync_is_authoritative_and_preserves_protected(self):
        from x_scout.models import RawAuthor
        with tempfile.TemporaryDirectory() as td:
            db = ScoutDatabase(path=Path(td) / "scout.sqlite3")
            # Protect an existing follow before the live sync.
            existing = RawAuthor("1", "keeper", "Keeper", "", True, "blue", 1000)
            db.follow_author(existing, source="manual")
            db.set_protected("keeper", True)

            following = [
                RawAuthor("1", "keeper", "Keeper", "", True, "blue", 1000),
                RawAuthor("2", "newfollow", "New Follow", "", True, "blue", 800),
            ]
            followers = [
                RawAuthor("1", "keeper", "Keeper", "", True, "blue", 1000),
                RawAuthor("3", "followeronly", "Follower Only", "", True, "blue", 900),
            ]
            a, b = db.sync_live_network(following, followers)
            self.assertEqual((a, b), (2, 2))
            self.assertEqual(db.relationship("keeper"), "MUTUAL")
            row = next(r for r in db.network_rows() if r["username"] == "keeper")
            self.assertTrue(row["protected"])
            self.assertEqual(db.relationship("newfollow"), "GRACE")
            self.assertEqual(db.relationship("followeronly"), "FOLLOWS YOU")
