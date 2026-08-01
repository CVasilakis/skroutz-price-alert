from unittest import mock

from core.notifications.apprise import AppriseNotifier
from core.notifications.templates import TITLE_PRICE_DROP, TITLE_STATUS_UPDATE
from core.scrapers.api import TrackedItem, UrlField

URL = UrlField("url", domains=("x",), accepts_url=lambda _url: True)


def _notifier(urls=("x://y",), *, added=True):
    with mock.patch("core.notifications.apprise.apprise.Apprise") as cls:
        app = cls.return_value
        app.add.return_value = added
        notifier = AppriseNotifier(urls)
        cls.instantiate.assert_not_called()
    return notifier, app


def _item(index=1):
    return TrackedItem(
        str(index),
        f"P{index}",
        0,
        _custom={URL: f"https://x/{index}"},
    )


def test_service_gate_registration_and_dispatch_exception():
    notifier, app = _notifier(added=False)
    assert not notifier.has_services
    app.add.assert_called_once_with("x://y")

    notifier, app = _notifier()
    assert notifier.has_services
    app.notify.return_value = 1
    assert notifier.notify("T", "B") is True
    app.notify.side_effect = RuntimeError
    assert notifier.notify("T", "B") is False


def test_price_drop_uses_selected_plugin_display_name():
    notifier, app = _notifier()
    app.notify.return_value = True
    assert notifier.notify_low_price("Store", "Widget", 12, 9, "https://x/1", "EUR")
    assert app.notify.call_args.kwargs["title"] == TITLE_PRICE_DROP
    assert "Store" in app.notify.call_args.kwargs["body"]


def test_reminder_variants_and_error_summary_truncation():
    notifier, app = _notifier()
    app.notify.return_value = True
    for update, phrase in (
        (True, "update is available"),
        (False, "latest version"),
        (None, "update check failed"),
    ):
        assert notifier.notify_reminder(update, "1 month", "soon")
        assert phrase in app.notify.call_args.kwargs["body"]
        assert app.notify.call_args.kwargs["title"] == TITLE_STATUS_UPDATE
        if update is True:
            assert (
                'run "./scrooge-alert update" to install it' in app.notify.call_args.kwargs["body"]
            )
    failures = [(_item(i), ValueError("boom")) for i in range(5)]
    assert notifier.notify_errors("Store", failures)
    body = app.notify.call_args.kwargs["body"]
    assert "... and 2 more errors." in body
    assert not notifier.notify_errors("Store", [])


def test_stale_items_crash_and_ping_delegation():
    notifier, app = _notifier()
    app.notify.return_value = True
    assert notifier.notify_stale_items("Store", [_item()], 48)
    assert notifier.notify_crash()
    server = mock.Mock()
    server.url.return_value = "tgram://token/chat"
    server.notify.return_value = True
    app.servers = [server]
    assert notifier.notify_test() == [("tgram://token/...", True)]
    server.notify.side_effect = RuntimeError
    assert notifier.notify_test() == [("tgram://token/...", False)]
