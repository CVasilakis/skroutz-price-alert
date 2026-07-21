from unittest import mock

from core.application.preflight import validate_notification_preflight
from core.constants import EXIT_CODE_NOTIFICATION_CONFIG_ERROR
from core.general.configuration import GeneralConfigLoad
from core.notifications.configuration import NotificationConfig
from core.settings import ResolvedSettings


def _general(notifications, *, permission_warning=None):
    return GeneralConfigLoad(
        notifications=notifications,
        settings=ResolvedSettings(()),
        permission_warning=permission_warning,
    )


def test_quiet_preflight_requires_one_valid_notification_url():
    general = _general(NotificationConfig(error="Notifications must be an object"))
    logger = mock.Mock()
    with (
        mock.patch("core.application.preflight.get_target_logger", return_value=logger),
        mock.patch("core.application.preflight.logging.critical") as critical,
    ):
        result = validate_notification_preflight(["alpha"], general)

    assert result == EXIT_CODE_NOTIFICATION_CONFIG_ERROR
    logger.error.assert_called_once()
    critical.assert_called_once()


def test_quiet_preflight_allows_mixed_urls_and_logs_only_invalid_count():
    general = _general(
        NotificationConfig(
            valid_urls=("json://localhost",),
            invalid_urls=("broken", "also-broken"),
        ),
        permission_warning="chmod 600",
    )
    logger = mock.Mock()
    with mock.patch("core.application.preflight.get_target_logger", return_value=logger):
        result = validate_notification_preflight(["alpha"], general)

    assert result is None
    logger.warning.assert_called_once()
    assert "2 invalid notification URL(s)" in logger.warning.call_args.args[0]
    assert "chmod" not in logger.warning.call_args.args[0]
