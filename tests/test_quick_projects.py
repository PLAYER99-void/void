"""Tests for app/quick.py's projects: a project you add can be found again by the same name.

    python3 -m unittest discover -s tests
"""
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "app"))
import quick  # noqa: E402


class ProjectNames(unittest.TestCase):
    def setUp(self):
        # quick.cfg / quick.save_config are filled in by server.py; use an in-memory config so the real one is never touched
        self.saved = (quick.cfg, quick.save_config)
        self.cfg = {}
        quick.cfg = self.cfg
        quick.save_config = self.cfg.update
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def tearDown(self):
        quick.cfg, quick.save_config = self.saved

    def mkdir(self, name):
        p = Path(self.tmp.name) / name
        p.mkdir()
        return str(p)

    def test_directory_word_stored_the_way_it_is_looked_up(self):
        """Adding and finding normalise the name the same way, so the lookup is an exact hit, not a lucky substring."""
        foo = self.mkdir("foo-src")
        key, path = quick.add_project("foo directory", foo)
        self.assertEqual(path, foo)
        self.assertEqual(quick._find_project("foo directory"), (key, foo))
        self.assertEqual(key, "foo")                 # what _find_project("foo directory") normalises to
        self.assertEqual(quick.projects(), {"foo": foo})

    def test_directory_word_not_shadowed_by_similar_project(self):
        """"foo directory" finds foo, even when another project ("food") contains the looked-up name."""
        food = self.mkdir("food-src")
        foo = self.mkdir("foo-src")
        quick.add_project("food", food)
        quick.add_project("foo directory", foo)
        hit = quick._find_project("foo directory")
        self.assertIsNotNone(hit)
        self.assertEqual(hit[1], foo)


if __name__ == "__main__":
    unittest.main()
