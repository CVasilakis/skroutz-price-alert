"""Drift guards for the scenario catalog's presentation metadata.

Tags and section labels are review/navigation content only (the gallery's ``--tag``
filter and the HTML report's chips and section headers) — nothing here touches the
snapshot gate. These tests keep that content from rotting the way it once did:
free-form tags accumulating near-synonyms (``failure`` vs ``error``) and opaque
one-offs (``whole``), and surfaces without a human-readable label.
"""

import unittest
from collections import Counter

from ui.catalog import ALL_SCENARIOS, Surface, SURFACE_INFO, TAG_VOCABULARY


class TestTagVocabulary(unittest.TestCase):
    def test_every_scenario_tag_is_in_the_vocabulary(self):
        # A tag outside TAG_VOCABULARY is either a typo or an undeclared addition;
        # both must fail loudly instead of silently minting a new filter chip.
        unknown = {
            (sc.snapshot_key, tag)
            for sc in ALL_SCENARIOS for tag in sc.tags
            if tag not in TAG_VOCABULARY
        }
        self.assertEqual(unknown, set(),
                         "unknown tag(s); add to TAG_VOCABULARY (catalog/_base.py) "
                         "with a one-line meaning, or fix the typo")

    def test_every_vocabulary_tag_is_used(self):
        # A vocabulary entry no scenario carries renders a dead filter button in the
        # report; retire it from TAG_VOCABULARY instead.
        used = Counter(tag for sc in ALL_SCENARIOS for tag in sc.tags)
        dead = set(TAG_VOCABULARY) - set(used)
        self.assertEqual(dead, set(), "vocabulary tag(s) used by no scenario")

    def test_no_tag_duplicated_within_a_scenario(self):
        for sc in ALL_SCENARIOS:
            self.assertEqual(len(sc.tags), len(set(sc.tags)),
                             f"{sc.snapshot_key} repeats a tag: {sc.tags}")


class TestSurfaceInfo(unittest.TestCase):
    def test_every_surface_has_info(self):
        # The report and gallery render SURFACE_INFO[surface] unconditionally, so a
        # new Surface member without a label/blurb must fail here, not at render time.
        self.assertEqual(set(SURFACE_INFO), set(Surface))

    def test_labels_and_blurbs_are_nonempty(self):
        for surface, info in SURFACE_INFO.items():
            self.assertTrue(info.label.strip(), f"{surface} has a blank label")
            self.assertTrue(info.blurb.strip(), f"{surface} has a blank blurb")


class TestGalleryVisibility(unittest.TestCase):
    """Test-only scenarios (in_gallery=False) stay out of review output by default.

    The STARTUP transcripts exist for the outside-panels assertion and their golden
    snapshots; every panel they stack is already reviewed on its own surface, so the
    gallery and the HTML report hide them unless the surface is requested explicitly.
    """

    def test_hidden_scenarios_are_excluded_by_default(self):
        from ui.gallery import _filtered
        shown = _filtered(None, None)
        self.assertTrue(all(sc.in_gallery for sc in shown))
        # And something actually is hidden, so this test can't pass vacuously.
        self.assertLess(len(shown), len(ALL_SCENARIOS))

    def test_hidden_scenarios_render_when_their_surface_is_explicit(self):
        from ui.gallery import _filtered
        startup = _filtered("startup", None)
        self.assertTrue(startup, "explicit --surface startup should still render")
        self.assertTrue(all(sc.surface is Surface.STARTUP for sc in startup))


class TestSectionOrder(unittest.TestCase):
    def test_registration_groups_each_surface_contiguously(self):
        # Report sections are first-seen registration order; an interleaved surface
        # would split into a confusing duplicate section.
        seen: list[Surface] = []
        for sc in ALL_SCENARIOS:
            if not seen or sc.surface != seen[-1]:
                seen.append(sc.surface)
        self.assertEqual(len(seen), len(set(seen)),
                         f"a surface registers non-contiguously: {[s.value for s in seen]}")

    def test_registration_order_matches_surface_member_order(self):
        # catalog/__init__.py's import order (what the report shows) must agree with
        # the Surface member order (what the enum documents as the display order).
        seen: list[Surface] = []
        for sc in ALL_SCENARIOS:
            if sc.surface not in seen:
                seen.append(sc.surface)
        self.assertEqual(seen, list(Surface))


if __name__ == "__main__":
    unittest.main()
