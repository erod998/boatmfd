"""Tests for the digital switching panel.

    python -m unittest discover -s tests -t . -v
"""
import tempfile
import unittest
from pathlib import Path

from app.switching import DEFAULT_CIRCUITS, SwitchingPanel


class TestSwitchingPanel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "switching.json"
        self.panel = SwitchingPanel(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_starts_with_the_default_circuits_all_off(self):
        rows = self.panel.list()
        self.assertEqual(len(rows), len(DEFAULT_CIRCUITS))
        self.assertTrue(all(not r["on"] for r in rows))

    def test_set_turns_a_circuit_on(self):
        self.panel.set("nav_lights", True)
        row = next(r for r in self.panel.list() if r["id"] == "nav_lights")
        self.assertTrue(row["on"])

    def test_set_an_unknown_circuit_raises(self):
        with self.assertRaises(ValueError):
            self.panel.set("warp_drive", True)

    def test_other_circuits_are_unaffected(self):
        self.panel.set("bilge_pump", True)
        rows = {r["id"]: r["on"] for r in self.panel.list()}
        self.assertTrue(rows["bilge_pump"])
        self.assertFalse(rows["nav_lights"])

    def test_state_survives_a_restart(self):
        self.panel.set("horn", True)
        reloaded = SwitchingPanel(self.path)
        row = next(r for r in reloaded.list() if r["id"] == "horn")
        self.assertTrue(row["on"])

    def test_a_damaged_file_falls_back_to_defaults(self):
        self.path.write_text("not json")
        panel = SwitchingPanel(self.path)
        self.assertEqual(len(panel.list()), len(DEFAULT_CIRCUITS))
        self.assertTrue(all(not r["on"] for r in panel.list()))
