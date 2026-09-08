import unittest
from x_scout.topic_profiles import analyse_description

class TopicFinderTests(unittest.TestCase):
    def test_classic_car_profile(self):
        p = analyse_description("classic car restoration, old Chevys, engine rebuilds and project cars")
        joined = " ".join(p.wanted_terms).lower()
        self.assertIn("classic car", joined)
        self.assertTrue(p.query_groups)

    def test_custom_profile_is_nonempty(self):
        p = analyse_description("ceramic pottery glazing kiln techniques studio artists", "spam, politics")
        self.assertGreaterEqual(len(p.wanted_terms), 3)
        self.assertIn("politics", [x.lower() for x in p.blocked_terms])
