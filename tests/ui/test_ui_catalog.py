"""Drift guards for the scenario catalog's presentation metadata.

Tags and section labels are review/navigation content only (the gallery's ``--tag``
filter and the HTML report's chips and section headers) — nothing here touches the
snapshot gate. These tests keep that content from rotting the way it once did:
free-form tags accumulating near-synonyms (``failure`` vs ``error``) and opaque
one-offs (``whole``), and surfaces without a human-readable label.
"""

import unittest
from collections import Counter

from ui.catalog import ALL_SCENARIOS, SURFACE_INFO, TAG_VOCABULARY, Surface


class TestTagVocabulary(unittest.TestCase):
    def test_every_scenario_tag_is_in_the_vocabulary(self):
        # A tag outside TAG_VOCABULARY is either a typo or an undeclared addition;
        # both must fail loudly instead of silently minting a new filter chip.
        unknown = {
            (sc.snapshot_key, tag)
            for sc in ALL_SCENARIOS
            for tag in sc.tags
            if tag not in TAG_VOCABULARY
        }
        self.assertEqual(
            unknown,
            set(),
            "unknown tag(s); add to TAG_VOCABULARY (catalog/_base.py) "
            "with a one-line meaning, or fix the typo",
        )

    def test_every_vocabulary_tag_is_used(self):
        # A vocabulary entry no scenario carries renders a dead filter button in the
        # report; retire it from TAG_VOCABULARY instead.
        used = Counter(tag for sc in ALL_SCENARIOS for tag in sc.tags)
        dead = set(TAG_VOCABULARY) - set(used)
        self.assertEqual(dead, set(), "vocabulary tag(s) used by no scenario")

    def test_no_tag_duplicated_within_a_scenario(self):
        for sc in ALL_SCENARIOS:
            self.assertEqual(
                len(sc.tags), len(set(sc.tags)), f"{sc.snapshot_key} repeats a tag: {sc.tags}"
            )


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
    gallery and the HTML report hide them unless an explicit --surface or --tag
    filter matches them.
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

    def test_hidden_scenarios_render_when_a_tag_matches_them(self):
        # A tag audit (e.g. --tag layout) must not silently omit the hidden
        # scenarios that carry the tag — they are often the canonical guards.
        from ui.gallery import _filtered

        for sc in ALL_SCENARIOS:
            if not sc.in_gallery:
                for tag in sc.tags:
                    self.assertIn(
                        sc, _filtered(None, tag), f"--tag {tag} should reveal {sc.snapshot_key}"
                    )


class TestSectionOrder(unittest.TestCase):
    def test_sections_follow_surface_member_order(self):
        # ALL_SCENARIOS is sorted so sections follow the Surface member order (the
        # single source of truth). The change-run of surfaces equals list(Surface)
        # exactly when every surface has scenarios, appears contiguously (a split
        # surface would repeat in the run), and in enum order — one assertion covers
        # all three, guarding the sort in catalog/__init__.py.
        seen: list[Surface] = []
        for sc in ALL_SCENARIOS:
            if not seen or sc.surface != seen[-1]:
                seen.append(sc.surface)
        self.assertEqual(seen, list(Surface))


if __name__ == "__main__":
    unittest.main()
