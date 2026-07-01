"""Golden-snapshot regression gate for the terminal UI.

Renders every catalogued scenario and compares the result (a ``# border: <color>`` header
plus the plain-text panel) against a committed file in ``snapshots/``. Set
``UPDATE_SNAPSHOTS=1`` to (re)write the golden files after reviewing a change:

    UPDATE_SNAPSHOTS=1 PYTHONPATH=src/core ./venv/bin/python3 -m unittest discover -s tests
"""

import os
import sys
import unittest

# Mirror the existing tests: make src/core importable so the catalog's production imports
# resolve even when PYTHONPATH is not pre-set.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src", "core")))

from ui.catalog import ALL_SCENARIOS          # noqa: E402
from ui.harness.rendering import snapshot_body  # noqa: E402

SNAPSHOT_DIR = os.path.join(os.path.dirname(__file__), "snapshots")
UPDATE = os.environ.get("UPDATE_SNAPSHOTS") == "1"
_REGEN_HINT = (
    "Run: UPDATE_SNAPSHOTS=1 PYTHONPATH=src/core ./venv/bin/python3 -m unittest discover -s tests"
)


class TestScenarioRegistry(unittest.TestCase):
    """Sanity checks on the catalog itself."""

    def test_scenarios_registered(self):
        self.assertTrue(ALL_SCENARIOS, "No UI scenarios are registered.")

    def test_keys_are_unique(self):
        keys = [s.snapshot_key for s in ALL_SCENARIOS]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        self.assertEqual(dupes, [], f"Duplicate scenario keys (surface+name must be unique): {dupes}")


class TestUISnapshots(unittest.TestCase):
    """Each scenario's rendered output matches its golden snapshot."""

    def test_matches_snapshot(self):
        os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        for sc in ALL_SCENARIOS:
            with self.subTest(scenario=sc.snapshot_key):
                body = snapshot_body(sc.build())
                path = os.path.join(SNAPSHOT_DIR, sc.snapshot_key + ".txt")

                if UPDATE:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(body)
                    continue

                self.assertTrue(
                    os.path.exists(path),
                    f"Missing snapshot for '{sc.snapshot_key}'. {_REGEN_HINT}",
                )
                with open(path, encoding="utf-8") as f:
                    expected = f.read()
                self.assertEqual(
                    body, expected,
                    f"UI output changed for '{sc.snapshot_key}'. "
                    f"If this is intended, review the diff and regenerate. {_REGEN_HINT}",
                )

    def test_no_orphan_snapshots(self):
        """A snapshot with no owning scenario is a leftover and must be removed."""
        if UPDATE or not os.path.isdir(SNAPSHOT_DIR):
            self.skipTest("nothing to check")
        keys = {s.snapshot_key for s in ALL_SCENARIOS}
        for filename in sorted(os.listdir(SNAPSHOT_DIR)):
            if filename.endswith(".txt"):
                with self.subTest(snapshot=filename):
                    self.assertIn(
                        filename[:-4], keys,
                        f"Orphan snapshot '{filename}' has no scenario. "
                        f"Delete it, or restore the scenario it belonged to.",
                    )


if __name__ == "__main__":
    unittest.main()
