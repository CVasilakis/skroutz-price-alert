import logging
from unittest import mock

from core.application.contracts import ConfigOutcome, PriceOutcome
from core.application.reporting import SilentRunReporter
from core.settings import ResolvedSetting, ResolvedSettings, SettingSpec, SettingStatus


def test_silent_reporter_covers_config_settings_and_result_levels():
    logger = mock.create_autospec(logging.Logger, instance=True)
    reporter = SilentRunReporter()
    mode = SettingSpec("mode", str, default="slow")
    limit = SettingSpec("limit", int, default=2, warning="bad limit")
    settings = ResolvedSettings(
        (
            (mode, ResolvedSetting("fast", SettingStatus.OK)),
            (limit, ResolvedSetting(2, SettingStatus.INVALID)),
        )
    )

    reporter.start_target("Store", logger, settings, ConfigOutcome(3))
    reporter.start_target("Store", logger, ResolvedSettings(()), ConfigOutcome(2, (1, 3)))
    reporter.start_target(
        "Store", logger, ResolvedSettings(()), ConfigOutcome(0, error="bad config")
    )
    reporter.start_scraping("Item", 1, 3)
    reporter.complete_scraping()
    reporter.log_result("✅", "Item", "Done", ["one", "two."])
    reporter.log_price_result("Item", 4, "EUR", 5, PriceOutcome.DROP)
    reporter.log_price_result(
        "Item",
        None,
        "EUR",
        5,
        PriceOutcome.NO_MATCH,
        notes="delivery failed",
        delivery_failed=True,
    )
    reporter.log_warning("Item", "warning", "detail")
    reporter.log_error("Item", "error", ["detail"])
    reporter.log_system_error("system error")
    reporter.log_attempt("Item", 2, 3, "ServerError")
    reporter.log_failure("Item", "RuntimeError", extra_notes=["trace saved"])
    reporter.start_sleep(1, 2, 3)
    reporter.update_sleep(0.5)
    reporter.complete_sleep(1)
    reporter.log_interrupt("stopped")
    reporter.complete_target()
    reporter.log_interrupt("outside target")

    assert logger.info.called
    assert logger.warning.called
    assert logger.error.called


def test_silent_reporter_ignores_rows_without_target_logger():
    reporter = SilentRunReporter()
    reporter.log_result("✅", "Item", "Done")
    reporter.log_price_result("Item", 1, "EUR", 2, PriceOutcome.OK)
    reporter.log_warning("Item", "warning")
    reporter.log_error("Item", "error")
    reporter.log_system_error("system error")
    reporter.log_attempt("Item", 1, 3, "detail")
    reporter.log_failure("Item", "error")


def test_silent_system_error_preserves_existing_log_wording():
    logger = mock.create_autospec(logging.Logger, instance=True)
    reporter = SilentRunReporter()
    reporter.start_target("Store", logger, ResolvedSettings(()), ConfigOutcome(1))
    logger.reset_mock()

    reporter.log_system_error("Another instance is currently running. Aborting...")

    logger.error.assert_called_once_with(
        "❗ System: Another instance is currently running. Aborting..."
    )
