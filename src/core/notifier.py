import apprise
from urllib.parse import urlparse
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any
from core.utils import is_valid_apprise_url
from core.scrapers.registry import ScraperRegistry

if TYPE_CHECKING:
    from core.scrapers.base.model import BaseTrackedItem
    from core.scrapers.base.plugin import BasePlugin

# The fixed notification titles, exported so tests assert against the same
# constants the sends use (one home per wording; the per-site titles below
# stay inline as f-strings).
TITLE_PRICE_DROP = 'Scrooge Alert - Price Drop!'
TITLE_STATUS_UPDATE = 'Scrooge Alert - Status Update'
TITLE_CRASH = 'Scrooge Alert - Script Crash'
TITLE_TEST = 'Scrooge Alert - Test Notification'


class Notifier:
    """Handles sending notifications via configured Apprise URLs."""
    def __init__(self, notification_urls: str,
                 plugin_for_url: "Callable[[str], BasePlugin | None] | None" = None):
        """Initializes the Notifier with a list of notification URLs.

        Args:
            notification_urls (str): A comma-separated list of Apprise notification URLs.
            plugin_for_url (Callable | None): Resolves a product URL to its plugin, for
                the display name in message bodies. Defaults to
                ``ScraperRegistry.plugin_for_url`` — the injection seam (mirroring the
                orchestrator's ``now_fn``) exists so tests and non-scraper callers can
                use the Notifier without triggering plugin discovery as a side effect.
        """
        self._plugin_for_url = plugin_for_url or ScraperRegistry.plugin_for_url
        self.app_notif = apprise.Apprise()
        self.has_services = False
        if notification_urls:
            for url in notification_urls.split(','):
                url = url.strip()
                if is_valid_apprise_url(url) and self.app_notif.add(url):
                    self.has_services = True

    def _extract_site(self, url: str) -> str:
        """Returns a human-readable store name for a product URL.

        Prefers the authoritative display name from the URL's registered plugin,
        so the brand is correct even when it differs from the domain label. Falls
        back to deriving a name from the domain only when no plugin matches the URL.
        """
        if not url:
            return "Unknown Site"

        plugin = self._plugin_for_url(url)
        if plugin is not None:
            return plugin.get_display_name()

        try:
            domain = urlparse(url).netloc.lower()
            if domain.startswith('www.'):
                domain = domain[4:]
            if not domain:
                return "Unknown Site"
            # Fallback: capitalize the main domain label (e.g. 'example.gr' -> 'Example')
            return domain.split('.')[0].capitalize()
        except Exception:
            return "Unknown Site"

    def notify(self, title: str, body: str) -> bool:
        """Sends a notification with the given title and body.

        Args:
            title (str): The title of the notification.
            body (str): The main content/body of the notification.

        Returns:
            bool: True if the notification was sent successfully, False otherwise.
        """
        return bool(self.app_notif.notify(title=title, body=body))

    def notify_low_price(self, product_name: str, target_price: float, current_price: float, url: str, currency: str = '€', advert_title: str | None = None) -> bool:
        """Sends a notification about a price drop below the target price.

        Args:
            product_name (str): The name of the product.
            target_price (float): The target price set by the user.
            current_price (float): The current price of the product.
            url (str): The URL of the product — for a listing-type match, the
                direct link to the advert itself.
            currency (str): The currency symbol. Defaults to '€'.
            advert_title (str | None): For a listing-type match, the advert's own
                title, shown on its own line so the user can tell which advert
                triggered the alert. None (default) for classic product scrapes.

        Returns:
            bool: True if the notification was sent successfully, False otherwise.
        """
        site = self._extract_site(url)
        advert_line = f'\nAdvert: {advert_title}' if advert_title else ''
        return self.notify(
            title=TITLE_PRICE_DROP,
            body=f'{product_name} is now available for {current_price}{currency} in {site}, which is below your target of {target_price}{currency}.{advert_line}\nView it here: {url}'
        )

    def _build_summary(self, title: str, header: str, items: Sequence[Any], format_item: Callable[[Any], str], footer: str, more_noun: str = "", max_show: int = 3) -> bool:
        """Builds and sends one aggregated summary notification.

        Shared shape for the aggregated notifications: a header line, up to
        ``max_show`` bullet rows, a truncation line when more remain, and a footer.
        Truncating caps the message size so a large batch can't bloat a push.

        Args:
            title (str): The notification title.
            header (str): The opening line (typically embeds the count and site).
            items (Sequence[Any]): The items to summarize (already filtered to a single site).
            format_item (Callable[[Any], str]): Maps one item to its bullet text
                (without the leading "- ").
            footer (str): The closing line.
            more_noun (str): Optional noun appended to the truncation line, e.g.
                " errors" yields "... and N more errors.". Defaults to "".
            max_show (int): Maximum number of bullet rows before truncating.

        Returns:
            bool: True if the notification was sent successfully, False otherwise.
        """
        body_lines = [header]

        for item in items[:max_show]:
            body_lines.append(f"- {format_item(item)}")

        if len(items) > max_show:
            remaining = len(items) - max_show
            body_lines.append(f"... and {remaining} more{more_noun}.")

        body_lines.append(footer)

        return self.notify(title=title, body="\n".join(body_lines))

    def notify_old_entries(self, stale_items: Sequence['BaseTrackedItem'], hours: int) -> bool:
        """Sends a single notification summarizing products that have gone stale.

        Aggregates every product that hasn't been successfully scraped within the
        threshold into one message. If many products are stale, the list is
        truncated to prevent notification bloat.

        Args:
            stale_items (Sequence[BaseTrackedItem]): The products whose last successful
                scrape is older than the threshold.
            hours (int): The staleness threshold in hours.

        Returns:
            bool: True if the notification was sent successfully, False otherwise.
        """
        if not stale_items:
            return False

        # Extract site name from the first stale item to give context
        site = self._extract_site(stale_items[0].url)
        return self._build_summary(
            title=f'Scrooge Alert - Tracking Stale on {site}',
            header=f"{len(stale_items)} product(s) on {site} haven't been successfully scraped in over {hours} hours:\n",
            items=stale_items,
            format_item=lambda item: f"{item.name}: {item.url}",
            footer="\nPlease check the error logs or verify the URLs are still valid.",
        )

    def notify_errors(self, failed_items: Sequence[tuple['BaseTrackedItem', Exception]]) -> bool:
        """Sends a notification indicating that specific errors occurred during scraping.

        Formats a summary of the failed products and their corresponding errors.
        If many errors occurred, the list is truncated to prevent notification bloat.

        Args:
            failed_items (Sequence[tuple[Product, Exception]]): A list of tuples containing
                the product that failed and the exception that caused the failure.

        Returns:
            bool: True if the notification was sent successfully, False otherwise.
        """
        if not failed_items:
            return False

        # Extract site name from the first failed item to give context
        site = self._extract_site(failed_items[0][0].url)
        return self._build_summary(
            title=f'Scrooge Alert - Scraping Errors on {site}',
            header=f"The script encountered errors while checking {len(failed_items)} product(s) on {site}:\n",
            items=failed_items,
            format_item=lambda pair: f"{pair[0].name}: {type(pair[1]).__name__}",
            footer="\nPlease review the error logs for more details.",
            more_noun=" errors",
        )

    def notify_reminder(self, update_available: bool | None, interval_display: str, next_due: str) -> bool:
        """Sends the periodic liveness reminder.

        Args:
            update_available (bool | None): Whether a project update is available, or
                ``None`` when the check was inconclusive (e.g. no network).
            interval_display (str): The configured cadence, user-facing (e.g. ``'1 month'``).
            next_due (str): The next due slot in local time, formatted in ``TIMESTAMP_FORMAT``.

        Returns:
            bool: True if the notification was sent successfully, False otherwise.
        """
        if update_available is True:
            update_line = 'A project update is available — run ./update.sh to install it.'
        elif update_available is False:
            update_line = 'You are running the latest version.'
        else:
            update_line = 'The update check failed; could not determine whether an update is available.'

        return self.notify(
            title=TITLE_STATUS_UPDATE,
            body=(
                f'This is your {interval_display} reminder that the scrapers are still running in the background.\n'
                f'{update_line}\n'
                f'Next reminder: on or shortly after {next_due} (local time).\n'
                'To disable these reminders, set "reminder": "off" in config/general.json.'
            )
        )

    def notify_crash(self) -> bool:
        """Sends a notification indicating that the script crashed unexpectedly.

        Returns:
            bool: True if the notification was sent successfully, False otherwise.
        """
        return self.notify(
            title=TITLE_CRASH,
            body='The script failed unexpectedly. Please review the error logs for more details on the crash.'
        )

    def notify_test(self) -> list:
        """Sends a test notification to all configured URLs to verify setup.

        Returns:
            list: A list of tuples containing the identifier and the success status (bool).
        """
        title = TITLE_TEST
        body = 'This is a test message to confirm that your Scrooge Alert notifications are configured correctly!'

        results = []
        for server in self.app_notif.servers:
            identifier = server.url(privacy=True)
            schema_end = identifier.find('://')
            if schema_end != -1:
                first_slash = identifier.find('/', schema_end + 3)
                if first_slash != -1:
                    identifier = identifier[:first_slash] + '/...'

            try:
                success = server.notify(title=title, body=body)
                results.append((identifier, success))
            except Exception:
                results.append((identifier, False))
        return results
