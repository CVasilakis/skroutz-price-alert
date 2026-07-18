from abc import ABC, abstractmethod
from typing import TYPE_CHECKING
from core.scrapers.base.model import BaseTrackedItem, ScrapeResult

if TYPE_CHECKING:
    from core.scrapers.base.settings import ResolvedSettings

class BaseScraperClient(ABC):
    """Abstract base class for scraping clients.

    Error-handling contract (this is what drives the orchestrator's
    retry / abort / notify behavior):
        ``scrape`` returns a typed success result or raises a modeled exception.
        The orchestrator branches on the exception *type*:

        * ``ProductNotFoundError`` / ``ProductUnavailableError`` /
          ``InvalidURLError`` — terminal for this item, NO retry; the item is
          rendered as a red product failure without aborting. Invalid URLs are
          included in the aggregated error notification.
        * ``ScraperParseError`` — retried (with ``refresh_identity`` between
          attempts); terminal exhaustion is counted and marks the run unhealthy.
        * ``RateLimitError`` — retried (with ``refresh_identity``); a terminal
          failure ABORTS the whole run for the target and saves a traceback.
        * ``ServerError`` — retried WITHOUT ``refresh_identity``; a terminal 5xx
          is shown and logged but intentionally NOT notified and NOT counted as a
          failure (a sustained outage instead surfaces via stale-entry tracking).
        * other modeled ``ScraperError`` values — retried and counted for the error
          notification, but do not turn a remote fault into an unhealthy run.
        * any non-``ScraperError`` ``Exception`` — treated as a plugin/programming
          fault; retried, counted, tracebacked, and marks the run unhealthy.

        A successful call must return a :class:`ScrapeResult`. To plug a new
        store into the retry machinery, raise these exceptions accordingly.

        Expected remote and parsing failures should use a :class:`ScraperError`
        subclass (see ``exceptions.py``). Any other exception type is treated as an
        unexpected fault: the orchestrator falls back to its default retry policy
        (retried, counted as a failure, traceback saved). When a store-specific
        parsing step can fail (e.g. coercing a price string to ``float``), wrap it
        and re-raise as :class:`ScraperParseError` so it maps to a modeled outcome.

    Settings access:
        The registry passes this client's target settings to the constructor (a
        :class:`~core.scrapers.base.settings.ResolvedSettings`), so a store-specific knob
        declared in the plugin definition is readable from ``__init__``
        onward — including during session/transport setup — e.g.
        ``self.settings.get("region")``. A subclass that overrides ``__init__`` must
        accept ``settings`` and forward it via ``super().__init__(settings)``.
        ``self.settings`` is ``None`` only when a client is constructed without it
        (e.g. a unit test); guard accordingly or rely on
        ``ResolvedSettings.get``'s default.
    """

    def __init__(self, settings: "ResolvedSettings | None" = None) -> None:
        """Stores the target's resolved settings.

        Args:
            settings (ResolvedSettings | None): The owning target's resolved
                settings, passed by the registry at instantiation. ``None`` when
                constructed outside the registry (e.g. a unit test).
        """
        self.settings = settings

    @abstractmethod
    def scrape(self, item: BaseTrackedItem) -> ScrapeResult:
        """Scrape one fully parsed tracked item.

        Args:
            item (BaseTrackedItem): The product or listing search to scrape.

        Returns:
            ScrapeResult: The result of the scrape.

        Raises:
            ProductNotFoundError: If the product is not found.
            ProductUnavailableError: If the product is found but price is unavailable.
            InvalidURLError: If the provided URL is invalid.
            ScraperParseError: If the response cannot be parsed.
            RateLimitError: If the request is blocked or rate limited.
            ServerError: For server-side (5xx) errors.
            ScraperError: For other scraping-related errors.

        See the class docstring for how each exception drives retry/abort/notify behavior.
        """
        ...

    def refresh_identity(self) -> None:
        """Resets headers, sessions, or cookies before a retry to evade blocks.

        Called by the orchestrator between retries for most error types. The base
        implementation is a no-op; a client with nothing to rotate (e.g. a simple
        ``requests``-based scraper) can rely on it and need not override this.
        """
        pass

    def close(self) -> None:
        """Closes any underlying sessions or resources.

        The base implementation is a no-op so clients without resources to release
        need not override it. Called once per run via ``ScraperRegistry.close_all``.
        """
        pass

    def get_current_headers(self) -> dict[str, str]:
        """Returns request headers for diagnostic logging (optional hook).

        Used only to annotate saved tracebacks. HTTP-based clients should return
        their active headers; non-HTTP clients can rely on the empty default.

        Returns:
            dict[str, str]: The current headers, or an empty dict.
        """
        return {}
