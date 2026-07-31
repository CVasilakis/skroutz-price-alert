"""Golden-snapshot regression gate for the terminal UI.

Renders every catalogued scenario and compares the result (a ``# border: <color>`` header
plus the plain-text panel) against a committed file in ``snapshots/terminal/``. Set
``UPDATE_SNAPSHOTS=1`` to (re)write the golden files after reviewing a change:

    UPDATE_SNAPSHOTS=1 ./venv/bin/python3 -m pytest
"""

import os
import unittest
from unittest import mock

from rich.text import Text
from shell.assertions import shell_tui_layout_errors

from ui.catalog import ALL_SCENARIOS, BACKGROUND_SURFACES
from ui.catalog._base import Surface
from ui.harness.rendering import (
    background_snapshot_body,
    capture_text,
    lines_outside_panels,
    snapshot_body,
)

SNAPSHOT_DIR = os.path.join(os.path.dirname(__file__), "snapshots")
TERMINAL_SNAPSHOT_DIR = os.path.join(SNAPSHOT_DIR, "terminal")
BACKGROUND_SNAPSHOT_DIR = os.path.join(SNAPSHOT_DIR, "background")
UPDATE = os.environ.get("UPDATE_SNAPSHOTS") == "1"
_REGEN_HINT = "Run: UPDATE_SNAPSHOTS=1 ./venv/bin/python3 -m pytest"


class TestScenarioRegistry(unittest.TestCase):
    """Sanity checks on the catalog itself."""

    def test_scenarios_registered(self):
        self.assertTrue(ALL_SCENARIOS, "No UI scenarios are registered.")

    def test_keys_are_unique(self):
        keys = [s.snapshot_key for s in ALL_SCENARIOS]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        self.assertEqual(
            dupes, [], f"Duplicate scenario keys (surface+name must be unique): {dupes}"
        )


class TestNoTextOutsidePanels(unittest.TestCase):
    """Every interactive-startup transcript keeps all output inside its panels.

    The STARTUP surface stacks the Configuration Check panel, the once-per-run reminder
    check, and the Scraping panel on a single console. A regression that prints a raw line
    to the console mid-run (e.g. a reminder warning that leaks to the terminal instead of
    its file log) lands *between* the panels; this catches it, complementing the golden
    snapshot, which shows the stray text visually in the diff.
    """

    def test_startup_transcripts_have_no_out_of_panel_text(self):
        startup = [s for s in ALL_SCENARIOS if s.surface is Surface.STARTUP]
        self.assertTrue(startup, "No STARTUP scenarios registered to guard.")
        for sc in startup:
            with self.subTest(scenario=sc.snapshot_key):
                stray = lines_outside_panels(capture_text(sc.build()))
                self.assertEqual(
                    stray,
                    [],
                    f"'{sc.snapshot_key}' printed text outside a panel: {stray}",
                )


class TestShellOperationalLayout(unittest.TestCase):
    """Non-debug shell transcripts obey the shared section grammar."""

    def test_non_debug_transcripts_have_well_formed_sections(self):
        shell_scenarios = [
            scenario
            for scenario in ALL_SCENARIOS
            if scenario.surface.value.startswith("sh-") and not scenario.shell_debug
        ]
        self.assertTrue(shell_scenarios, "No non-debug shell scenarios registered to guard.")

        for scenario in shell_scenarios:
            with self.subTest(scenario=scenario.snapshot_key):
                result = scenario.build()
                self.assertIsInstance(result.renderable, Text)
                errors = shell_tui_layout_errors(result.renderable.plain)
                self.assertEqual(
                    errors,
                    (),
                    f"'{scenario.snapshot_key}' broke the non-debug shell TUI layout",
                )


class TestSnapshotRenderingDeterminism(unittest.TestCase):
    """Ambient terminal preferences must not change committed snapshot text."""

    def test_dumb_terminal_does_not_override_recording_console_width(self):
        scenario = next(
            sc for sc in ALL_SCENARIOS if sc.snapshot_key == "sh-enable__catalog_unavailable"
        )

        with mock.patch.dict(os.environ, {"TERM": "xterm-256color"}):
            normal = snapshot_body(scenario.build())

        with mock.patch.dict(os.environ, {"TERM": "dumb"}):
            dumb_terminal = snapshot_body(scenario.build())

        self.assertEqual(dumb_terminal, normal)

    def test_no_color_environment_does_not_change_progress_bar_glyphs(self):
        scenario = next(sc for sc in ALL_SCENARIOS if sc.snapshot_key == "run__sleeping_pacing")

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NO_COLOR", None)
            normal = snapshot_body(scenario.build())

        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            no_color_host = snapshot_body(scenario.build())

        self.assertEqual(no_color_host, normal)


class TestUISnapshots(unittest.TestCase):
    """Each scenario's rendered output matches its golden snapshot."""

    def test_matches_snapshot(self):
        os.makedirs(TERMINAL_SNAPSHOT_DIR, exist_ok=True)
        os.makedirs(BACKGROUND_SNAPSHOT_DIR, exist_ok=True)
        for sc in ALL_SCENARIOS:
            with self.subTest(scenario=sc.snapshot_key):
                result = sc.build()
                body = snapshot_body(result)
                path = os.path.join(TERMINAL_SNAPSHOT_DIR, sc.snapshot_key + ".txt")

                if UPDATE:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(body)
                else:
                    self.assertTrue(
                        os.path.exists(path),
                        f"Missing snapshot for '{sc.snapshot_key}'. {_REGEN_HINT}",
                    )
                    with open(path, encoding="utf-8") as f:
                        expected = f.read()
                    self.assertEqual(
                        body,
                        expected,
                        f"UI output changed for '{sc.snapshot_key}'. "
                        f"If this is intended, review the diff and regenerate. {_REGEN_HINT}",
                    )

                should_log = sc.surface in BACKGROUND_SURFACES
                self.assertEqual(
                    bool(result.output_logs),
                    should_log,
                    f"'{sc.snapshot_key}' background-log applicability changed",
                )
                if should_log:
                    background_body = background_snapshot_body(result)
                    background_path = os.path.join(
                        BACKGROUND_SNAPSHOT_DIR, sc.snapshot_key + ".txt"
                    )
                    if UPDATE:
                        with open(background_path, "w", encoding="utf-8") as f:
                            f.write(background_body)
                    else:
                        self.assertTrue(
                            os.path.exists(background_path),
                            f"Missing background snapshot for '{sc.snapshot_key}'. {_REGEN_HINT}",
                        )
                        with open(background_path, encoding="utf-8") as f:
                            expected_background = f.read()
                        self.assertEqual(
                            background_body,
                            expected_background,
                            f"Background logs changed for '{sc.snapshot_key}'. "
                            f"If this is intended, review the diff and regenerate. {_REGEN_HINT}",
                        )

    def test_no_orphan_snapshots(self):
        """A snapshot with no owning scenario is a leftover and must be removed."""
        if UPDATE or not os.path.isdir(SNAPSHOT_DIR):
            self.skipTest("nothing to check")
        keys = {s.snapshot_key for s in ALL_SCENARIOS}
        self.assertTrue(
            os.path.isdir(TERMINAL_SNAPSHOT_DIR),
            f"Missing terminal snapshot directory. {_REGEN_HINT}",
        )
        for filename in sorted(os.listdir(TERMINAL_SNAPSHOT_DIR)):
            if filename.endswith(".txt"):
                with self.subTest(snapshot=filename):
                    self.assertIn(
                        filename[:-4],
                        keys,
                        f"Orphan snapshot '{filename}' has no scenario. "
                        f"Delete it, or restore the scenario it belonged to.",
                    )

        background_keys = {
            scenario.snapshot_key
            for scenario in ALL_SCENARIOS
            if scenario.surface in BACKGROUND_SURFACES
        }
        self.assertTrue(
            os.path.isdir(BACKGROUND_SNAPSHOT_DIR),
            f"Missing background snapshot directory. {_REGEN_HINT}",
        )
        for filename in sorted(os.listdir(BACKGROUND_SNAPSHOT_DIR)):
            if filename.endswith(".txt"):
                with self.subTest(background_snapshot=filename):
                    self.assertIn(
                        filename[:-4],
                        background_keys,
                        f"Orphan background snapshot '{filename}' has no owning scenario. "
                        f"Delete it, or restore the scenario it belonged to.",
                    )


if __name__ == "__main__":
    unittest.main()
