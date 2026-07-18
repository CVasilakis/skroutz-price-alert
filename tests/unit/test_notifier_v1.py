from unittest import mock

from core.notifier import Notifier, TITLE_PRICE_DROP, TITLE_STATUS_UPDATE
from core.scrapers.api import TrackedItem


def _notifier(urls="x://y", *, valid=True, added=True):
    with mock.patch("core.notifier.apprise.Apprise") as cls, \
         mock.patch("core.notifier.is_valid_apprise_url", return_value=valid):
        app = cls.return_value
        app.add.return_value = added
        notifier = Notifier(urls)
    return notifier, app


def _item(index=1):
    return TrackedItem(str(index), f"P{index}", f"https://x/{index}", 0)


def test_service_gate_and_dispatch_exception():
    notifier, app = _notifier(valid=False)
    assert not notifier.has_services
    app.add.assert_not_called()
    notifier, app = _notifier()
    assert notifier.has_services
    app.notify.return_value = 1
    assert notifier.notify("T", "B") is True
    app.notify.side_effect = RuntimeError
    assert notifier.notify("T", "B") is False


def test_price_drop_and_site_resolution():
    notifier, app = _notifier()
    app.notify.return_value = True
    with mock.patch.object(notifier, "_extract_site", return_value="Store"):
        assert notifier.notify_low_price("Widget", 12, 9, "https://x/1", "EUR")
    assert app.notify.call_args.kwargs["title"] == TITLE_PRICE_DROP
    assert "Store" in app.notify.call_args.kwargs["body"]
    plugin = mock.Mock(display_name="Skroutz")
    notifier, _ = _notifier("")
    notifier._plugin_for_url = lambda _url: plugin
    assert notifier._extract_site("https://www.skroutz.gr/x") == "Skroutz"
    with mock.patch("core.notifier.ScraperRegistry.plugin_for_url", return_value=None):
        notifier._plugin_for_url = lambda _url: None
        assert notifier._extract_site("https://www.example.gr/x") == "Example"
        assert notifier._extract_site("garbage") == "Unknown Site"


def test_reminder_variants_and_error_summary_truncation():
    notifier, app = _notifier()
    app.notify.return_value = True
    for update, phrase in ((True, "update is available"), (False, "latest version"),
                           (None, "update check failed")):
        assert notifier.notify_reminder(update, "1 month", "soon")
        assert phrase in app.notify.call_args.kwargs["body"]
        assert app.notify.call_args.kwargs["title"] == TITLE_STATUS_UPDATE
    failures = [(_item(i), ValueError("boom")) for i in range(5)]
    with mock.patch.object(notifier, "_extract_site", return_value="Store"):
        assert notifier.notify_errors(failures)
    body = app.notify.call_args.kwargs["body"]
    assert "... and 2 more errors." in body
    assert not notifier.notify_errors([])


def test_old_entries_and_crash_and_ping_delegation():
    notifier, app = _notifier()
    app.notify.return_value = True
    with mock.patch.object(notifier, "_extract_site", return_value="Store"):
        assert notifier.notify_old_entries([_item()], 48)
    assert notifier.notify_crash()
    server = mock.Mock()
    server.url.return_value = "tgram://token/chat"
    server.notify.return_value = True
    app.servers = [server]
    assert notifier.notify_test() == [("tgram://token/...", True)]
    server.notify.side_effect = RuntimeError
    assert notifier.notify_test() == [("tgram://token/...", False)]
