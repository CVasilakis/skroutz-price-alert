from unittest import mock

from core.notifications.configuration import resolve_notification_config


def test_semantic_validation_happens_once_during_configuration_resolution():
    with mock.patch("apprise.Apprise.instantiate", return_value=object()) as instantiate:
        resolved = resolve_notification_config({"urls": ["json://localhost"]})

    assert resolved.valid_urls == ("json://localhost",)
    instantiate.assert_called_once_with("json://localhost")
