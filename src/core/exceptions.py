"""Modeled failures shared by the scraping domain and the surrounding runtime.

The first group is re-exported through :mod:`core.scrapers.api` and is the
vocabulary a plugin client raises. Choosing one of them is the only influence a
plugin has over what happens next: retrying, aborting, notifying, and the
process exit status are owned by the application layer
(``core.application.retry`` classifies them, ``core.application.items`` applies
the classification). The policy each docstring records below is that behavior,
not a second implementation of it.
"""


class ScraperError(Exception):
    """Base exception for scraping related errors.

    Raise it for a modeled scraping failure that none of the subclasses below
    describes. The item is retried up to the run's attempt limit; if the last
    attempt still fails, a traceback is written to ``logs/<target>/errors.txt``
    and the item is listed in the scraping-errors notification. It does not by
    itself change the process exit status.
    """

    pass


class RateLimitError(ScraperError):
    """Raised when the scraper is rate limited or blocked.

    The most severe modeled failure: after the attempts are exhausted it aborts
    the target, so the remaining items are not requested at all, and the run
    exits ``17``. A traceback is saved. Reserve it for evidence that the host is
    refusing traffic (429, an anti-bot challenge, a block page) rather than for
    one resource being unavailable.
    """

    pass


class ServerError(ScraperError):
    """Raised when the server returns a 5xx error.

    Treated as a remote condition rather than a plugin fault: the item is
    retried, :meth:`~core.scrapers.api.ScraperClient.prepare_retry` is skipped
    because rotating a request identity cannot fix the far side, and an
    exhausted item -- while still shown in the run output -- is neither counted
    as a plugin failure nor given an exit status. A genuine outage still reaches
    the user through the stale-tracking notification once the item has gone
    unchecked for long enough.
    """

    pass


class ScraperParseError(ScraperError):
    """Raised when the scraper fails to parse the response data.

    The expected failure when a store changes its markup or payload. The item is
    retried and, once exhausted, is reported and exits ``18``. No traceback is
    saved: the message is the diagnostic, so include what was actually received
    (a status, a missing key, an unparseable price) rather than a bare
    "parse failed".
    """

    pass


class InvalidScrapeResultError(ScraperParseError):
    """Raised when a scraper returns a value that violates result invariants.

    Raised by the result types themselves -- a blank currency or title, a
    boolean, negative, or non-finite price, a non-``Offer`` listing member, a
    relative offer URL -- and by the boundary check around ``scrape()``. Plugins
    normally do not raise it directly. Being a parse error, it is retried and
    never reaches state or a notification, so an invalid result can neither
    persist a wrong price nor alert on one.
    """

    pass


class ResourceNotFoundError(ScraperError):
    """Raised when a requested resource is not found or has been removed.

    A skip rather than a failure: the item is reported as skipped immediately,
    without retrying, without an alert, and without affecting the exit status,
    because retrying a removed page cannot succeed. The run continues with the
    next item.
    """

    pass


class PriceUnavailableError(ScraperError):
    """Raised when a resource is found but has no price available.

    The out-of-stock / no-offer case, handled exactly like
    :class:`ResourceNotFoundError`: skipped at once, no retry, no alert, no exit
    status. Prefer it over returning a placeholder price, which would be stored
    and could trigger a false alert.
    """

    pass


class InvalidURLError(ScraperError):
    """Raised when the provided URL is invalid or unparsable.

    Skipped without retrying like the two above, but unlike them it is reported
    in the scraping-errors notification, since it usually means the user's row
    needs fixing. The framework has already validated and canonicalized every
    declared :class:`~core.scrapers.api.UrlField`, so raise this only for a
    store-specific requirement a URL predicate cannot express -- a product ID
    that must be extractable from the path, for instance.
    """

    pass


class StorageFileError(Exception):
    """A concise storage failure paired with optional technical diagnostics.

    ``str(error)`` is deliberately presentation-safe. Callers may write
    :attr:`diagnostic_detail` to an error log, but must not place it in a panel.
    """

    def __init__(self, display_message: str, diagnostic_detail: str | None = None) -> None:
        if not isinstance(display_message, str) or not display_message.strip():
            raise ValueError("storage display message must be nonblank")
        if diagnostic_detail is not None and (
            not isinstance(diagnostic_detail, str) or not diagnostic_detail.strip()
        ):
            raise ValueError("storage diagnostic detail must be nonblank when provided")
        self.display_message = display_message.strip()
        self.diagnostic_detail = diagnostic_detail.strip() if diagnostic_detail else None
        super().__init__(self.display_message)


class ConfigFileError(StorageFileError):
    """Raised when strict user configuration cannot be loaded."""

    pass


class StateFileError(StorageFileError):
    """Raised when machine-owned state cannot be loaded or persisted."""

    pass


class LockStorageError(StorageFileError):
    """Raised when machine-owned cooperative lock storage cannot be used."""

    pass


class UpdateCheckError(Exception):
    """Raised when there is an issue checking for script updates."""

    pass


class LockAcquisitionError(Exception):
    """Raised when a lock cannot be acquired because it is held by another process."""

    pass


class PluginError(Exception):
    """Base class for plugin discovery, validation, and dependency failures."""

    pass


class PluginDiscoveryError(PluginError):
    """Raised when a scraper plugin package cannot be discovered or imported."""

    pass


class PluginValidationError(PluginError):
    """Raised when a discovered plugin definition violates its contract."""

    pass


class PluginDependencyError(PluginError):
    """Raised when a lazily loaded plugin dependency is not installed."""

    pass
