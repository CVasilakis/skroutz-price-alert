from unittest import mock

from core.application.pacing import Pacer


def test_pacer_reports_retry_delay_without_waiting_when_delay_is_zero():
    reporter = mock.Mock()
    pacer = Pacer(
        reporter,
        interrupted=lambda: False,
        monotonic_fn=lambda: 10.0,
        sleep_fn=mock.Mock(),
        jitter_fn=lambda _minimum, _maximum: 0.0,
    )

    pacer.sleep(0, is_retry=True)

    reporter.start_sleep.assert_called_once_with(0.0, 2, 3)
    reporter.complete_sleep.assert_called_once_with(0.0)


def test_pacer_stops_without_completing_when_interrupted():
    reporter = mock.Mock()
    sleep = mock.Mock()
    pacer = Pacer(
        reporter,
        interrupted=lambda: True,
        monotonic_fn=lambda: 0.0,
        sleep_fn=sleep,
        jitter_fn=lambda _minimum, _maximum: 1.0,
    )

    pacer.sleep(20)

    sleep.assert_not_called()
    reporter.complete_sleep.assert_not_called()
