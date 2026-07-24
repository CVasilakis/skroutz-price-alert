from unittest import mock

import pytest

from core.notifications.configuration import (
    NotificationValidationError,
    NotificationValidationProblem,
    resolve_notification_config,
)


def test_semantic_validation_happens_once_during_configuration_resolution():
    with mock.patch("apprise.Apprise.instantiate", return_value=object()) as instantiate:
        resolved = resolve_notification_config({"urls": ["json://localhost"]})

    assert resolved.valid_urls == ("json://localhost",)
    instantiate.assert_called_once_with("json://localhost")


@pytest.mark.parametrize(
    ("block", "problem", "message"),
    [
        ([], NotificationValidationProblem.NOT_OBJECT, "Notifications must be an object"),
        (
            {"unknown": []},
            NotificationValidationProblem.UNKNOWN,
            "Unknown notification settings: unknown",
        ),
        (
            {"urls": "json://localhost"},
            NotificationValidationProblem.URLS_NOT_ARRAY,
            'Notification setting "urls" must be an array',
        ),
        (
            {"urls": [1]},
            NotificationValidationProblem.URL_NOT_STRING,
            "Notification URL at JSON index 1 must be a string",
        ),
    ],
)
def test_structural_failures_have_stable_typed_categories(block, problem, message):
    with pytest.raises(NotificationValidationError) as caught:
        resolve_notification_config(block)

    assert caught.value.problem is problem
    assert str(caught.value) == message
