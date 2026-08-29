"""Provenance and redaction rules behind every settings row (core.presentation)."""

from core.presentation import resolved_setting_views
from core.settings import SettingSpec, SettingStatus, resolve_settings

OPTIONAL = SettingSpec[int]("limit", decode=int, default=5)
REQUIRED = SettingSpec[str]("region", decode=lambda raw: str(raw).strip())


def _view(spec, block):
    return resolved_setting_views(resolve_settings((spec,), block))[0]


def test_default_marker_tracks_provenance_not_the_displayed_value():
    """Only a declaration's default is labelled one, whatever value is shown."""
    configured = _view(OPTIONAL, {"limit": 4})
    assert configured.status is SettingStatus.OK
    assert not configured.is_default

    # A configured value that equals the default still came from the user, so the
    # row must stay distinguishable from an omitted one.
    same_as_default = _view(OPTIONAL, {"limit": 5})
    assert same_as_default.status is SettingStatus.OK
    assert same_as_default.display_value == "5"
    assert not same_as_default.is_default

    omitted = _view(OPTIONAL, {})
    assert omitted.status is SettingStatus.DEFAULT
    assert omitted.display_value == "5"
    assert omitted.is_default

    no_block = _view(OPTIONAL, None)
    assert no_block.status is SettingStatus.NO_CONFIG
    assert no_block.is_default


def test_unusable_values_are_reported_as_problems_never_as_defaults():
    """A row needing the user's attention is never also labelled a default.

    Both cases would otherwise read as settled: an invalid value does fall back to
    its default, and a required setting has none to fall back to at all. Neither
    may claim the marker, so a surface that only counts or filters on
    ``is_default`` cannot hide them.
    """
    invalid = _view(OPTIONAL, {"limit": "banana"})
    assert invalid.status is SettingStatus.INVALID
    assert invalid.display_value == "5"  # it did fall back to the default
    assert invalid.has_warning
    assert not invalid.is_default

    missing = _view(REQUIRED, {})
    assert missing.status is SettingStatus.MISSING
    assert missing.display_value == "required"
    assert missing.has_warning
    assert not missing.is_default
