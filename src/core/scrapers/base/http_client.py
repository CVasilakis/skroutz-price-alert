import random
from typing import TYPE_CHECKING

import tls_client
from tls_client.response import Response

from core import messages
from core.scrapers.api import ScraperClient
from core.exceptions import ScraperError, RateLimitError, ServerError, ProductNotFoundError

if TYPE_CHECKING:
    from core.settings import ResolvedSettings


class HttpScraperClient(ScraperClient):
    """Base client for HTTP-based scrapers (JSON APIs and HTML pages alike).

    Owns the transport boilerplate every HTTP scraper would otherwise copy:
        * a ``tls_client`` session whose browser fingerprint is recreated on
          ``refresh_identity`` (called by the orchestrator between retries),
        * a bounded ``get`` hook that applies an explicit whole-request deadline,
        * rotation over a pool of header profiles to vary the request identity, and
        * the canonical HTTP-status -> modeled-exception mapping
          (:meth:`raise_for_status`) that the orchestrator's ErrorPolicy table is
          keyed on, so a new store maps statuses correctly by default instead of
          re-deriving (and possibly mis-mapping) it.

    A subclass declares a non-empty ``HEADERS_POOL`` and implements
    ``scrape``. A JSON-API store calls ``get`` / ``raise_for_status`` then
    decodes JSON; an HTML store follows the same bounded fetch path and parses the
    markup itself — either way it gets timeout enforcement, status mapping, and
    identity rotation for free. Stores whose API uses
    non-standard status codes override the ``*_CODES`` class attributes (or
    :meth:`raise_for_status` entirely) rather than re-implementing the mapping.
    """

    #: Per-subclass pool of header profiles; one is chosen at random per identity.
    HEADERS_POOL: list[dict[str, str]] = [{}]
    #: tls_client browser fingerprint identifier.
    TLS_CLIENT_IDENTIFIER: str = "chrome120"
    #: Hard deadline for one complete request (connect, redirects, and body read).
    #: A store with a documented need for a longer response may override this value.
    REQUEST_TIMEOUT_SECONDS: int = 30

    #: HTTP status codes mapped to each modeled outcome (overridable per store).
    NOT_FOUND_CODES: tuple = (404, 410)
    RATE_LIMIT_CODES: tuple = (401, 403, 429)

    def __init__(self, settings: "ResolvedSettings") -> None:
        """Picks a random header profile and opens the initial TLS session.

        Args:
            settings (ResolvedSettings | None): The target's resolved settings
                (see BaseScraperClient); available to subclasses from here onward.
        """
        super().__init__(settings)
        # Copy the chosen profile so a subclass mutating self.current_headers in
        # place can never corrupt the shared class-level pool.
        self.current_headers: dict[str, str] = dict(random.choice(self.HEADERS_POOL))
        self.session = self._new_session()

    def _new_session(self) -> tls_client.Session:
        """Creates a fresh TLS session with a randomized extension order."""
        return tls_client.Session(
            client_identifier=self.TLS_CLIENT_IDENTIFIER,  # type: ignore
            random_tls_extension_order=True,
        )

    def get(self, url: str, headers: dict[str, str] | None = None) -> Response:
        """Performs a GET with the client's explicit whole-request deadline.

        ``tls_client`` exposes one deadline for the complete request lifecycle,
        rather than separate connect/read values. Keeping the deadline in this
        shared hook makes every HTTP plugin bounded without relying on the
        dependency's implicit default. Plugins must use this method instead of
        calling ``self.session.get`` directly.

        Args:
            url (str): The URL to request.
            headers (dict[str, str] | None): Request-specific headers.

        Returns:
            Response: The completed response.
        """
        return self.session.get(
            url,
            headers=headers,
            timeout_seconds=self.REQUEST_TIMEOUT_SECONDS,
        )

    def get_current_headers(self) -> dict[str, str]:
        """Returns the header profile currently in use (annotates saved tracebacks)."""
        return self.current_headers

    def refresh_identity(self) -> None:
        """Rotates to a new (copied) header profile and recreates the TLS session."""
        self.current_headers = dict(random.choice(self.HEADERS_POOL))
        self.session.close()
        self.session = self._new_session()

    def close(self) -> None:
        """Closes the underlying TLS session."""
        self.session.close()

    def raise_for_status(self, status_code: int | None) -> None:
        """Maps an HTTP status code to a modeled scraper exception.

        Returns normally for a 200 response; for anything else it raises the
        exception the orchestrator's retry/abort/notify policy is keyed on. See
        :class:`BaseScraperClient` for how each exception drives that behavior.

        Args:
            status_code (int | None): The response status code (``None`` when the
                request yielded no response).

        Raises:
            ScraperError: Missing status, or a non-200 code not covered below.
            ProductNotFoundError: A removed/not-found status (default 404, 410).
            RateLimitError: A blocked/rate-limited status (default 401, 403, 429).
            ServerError: Any 5xx server-side error.
        """
        if status_code is None:
            raise ScraperError(messages.EMPTY_RESPONSE_DETAIL)
        if status_code == 200:
            return
        if status_code in self.NOT_FOUND_CODES:
            raise ProductNotFoundError(messages.not_found_detail(status_code))
        if status_code in self.RATE_LIMIT_CODES:
            raise RateLimitError(messages.rate_limited_detail(status_code))
        if 500 <= status_code < 600:
            raise ServerError(messages.server_error_detail(status_code))
        raise ScraperError(messages.http_failed_detail(status_code))
