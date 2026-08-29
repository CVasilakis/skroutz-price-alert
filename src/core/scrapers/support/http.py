"""Optional reusable TLS transport for HTTP-based plugin clients.

Opt-in and store-independent: nothing in the framework requires it, and a plugin
that does not subclass :class:`HttpScraperClient` never loads it. Because this
module imports ``tls_client``, a plugin using it must declare ``tls-client`` in
its own colocated ``requirements.txt``, and may import this module only from
``client.py`` -- never from the import-light descriptor.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

import tls_client
from tls_client.response import Response

from core import messages
from core.scrapers.api import (
    RateLimitError,
    ResourceNotFoundError,
    ScraperClient,
    ScraperError,
    ServerError,
)

if TYPE_CHECKING:
    from core.settings import ResolvedSettings


class HttpScraperClient(ScraperClient):
    """Bounded GET transport with identity rotation and standard status mapping.

    Subclass it instead of :class:`~core.scrapers.api.ScraperClient` when the
    store is reached over HTTP, and implement only ``scrape()``: the session,
    per-retry identity rotation, clean shutdown, and the HTTP-status-to-exception
    mapping are inherited. Override the class attributes below to shape the
    transport; override the methods only for genuinely store-specific behavior.

    A typical ``scrape()`` builds request headers from
    :attr:`current_headers`, calls :meth:`get`, passes the status through
    :meth:`raise_for_status`, then parses the body.

    Example:
        ```python
        class Client(HttpScraperClient):
            HEADERS_POOL = _HEADERS_POOL

            def scrape(self, item: TrackedItem) -> PriceResult:
                response = self.get(item[SOURCE_URL], headers=self.current_headers.copy())
                self.raise_for_status(response.status_code)
                ...
        ```
    """

    HEADERS_POOL: list[dict[str, str]] = [{}]
    """Interchangeable header profiles, one of which is active at a time.

    Override it with realistic, complete browser profiles. One is chosen at
    random on construction and again on every :meth:`prepare_retry`, so a
    transient block does not keep hitting the store with the same fingerprint.
    The default single empty profile sends no extra headers at all.
    """

    TLS_CLIENT_IDENTIFIER = "chrome120"
    """The ``tls_client`` TLS/JA3 profile the session impersonates."""

    REQUEST_TIMEOUT_SECONDS = 30
    """Per-request ceiling. Bounded on purpose: a hung request would otherwise
    stall a sequential, paced run indefinitely."""

    NOT_FOUND_CODES = (404, 410)
    """Statuses mapped to :class:`~core.scrapers.api.ResourceNotFoundError`."""

    RATE_LIMIT_CODES = (401, 403, 429)
    """Statuses mapped to :class:`~core.scrapers.api.RateLimitError`.

    401 and 403 are included deliberately: stores commonly answer bot detection
    with them, so they are treated as the host refusing traffic rather than as a
    per-resource failure, and they abort the target like an explicit 429.
    Override the tuple for a store where they mean something else.
    """

    def __init__(self, settings: "ResolvedSettings") -> None:
        """Pick the first header profile and open the session for this target's run."""
        super().__init__(settings)

        self.current_headers = dict(random.choice(self.HEADERS_POOL))
        """The header profile active for the current attempt.

        A private copy of one :attr:`HEADERS_POOL` entry, replaced on every
        :meth:`prepare_retry`. Copy it before adding per-request entries so the
        pool profile itself stays reusable.
        """

        self.session = self._new_session()
        """The impersonating session shared by every request of this target's run."""

    def _new_session(self) -> tls_client.Session:
        """Create one impersonating session with a randomized TLS extension order."""
        return tls_client.Session(
            client_identifier=self.TLS_CLIENT_IDENTIFIER,  # type: ignore[arg-type]
            random_tls_extension_order=True,
        )

    def get(self, url: str, headers: dict[str, str] | None = None) -> Response:
        """Perform one bounded GET on the shared session.

        The transport stays deliberately thin: it sends exactly the headers it is
        given and never merges :attr:`current_headers` in on its own, because a
        client usually has to shape the active profile per request (host-specific
        ``authority``, a matching ``referer``). Pass ``headers=`` explicitly --
        omitting it sends no profile headers at all.

        Args:
            url: The absolute URL to request.
            headers: Request headers, typically a copy of
                :attr:`current_headers` with store-specific entries applied.

        Returns:
            The raw response. It is *not* status-checked; pass
            ``response.status_code`` to :meth:`raise_for_status` first.
        """
        return self.session.get(
            url,
            headers=headers,
            timeout_seconds=self.REQUEST_TIMEOUT_SECONDS,
        )

    def diagnostic_context(self) -> dict[str, str]:
        """Expose only coarse, non-secret identity details to error logs."""
        keys = ("accept-language", "sec-ch-ua-platform")
        return {key: self.current_headers[key] for key in keys if key in self.current_headers}

    def prepare_retry(self) -> None:
        """Rotate to another header profile and replace the session before a retry.

        Both halves matter: the profile changes the request fingerprint, and the
        fresh session drops pooled connections and any TLS state the far side may
        have associated with the previous attempt.
        """
        self.current_headers = dict(random.choice(self.HEADERS_POOL))
        self.session.close()
        self.session = self._new_session()

    def close(self) -> None:
        """Close the session; the framework calls this in the target's ``finally`` block."""
        self.session.close()

    def raise_for_status(self, status_code: int | None) -> None:
        """Translate one HTTP status into the framework's modeled exceptions.

        Centralizes the mapping so every HTTP plugin classifies failures the same
        way and inherits the same retry, abort, and reporting policy. Call it on
        every response before parsing.

        Only ``200`` is treated as success; every other 2xx or 3xx falls through
        to the generic failure, because a price parser has nothing to read from
        them.

        Args:
            status_code: The response status, or ``None`` when the transport
                produced no response at all.

        Raises:
            ScraperError: No response arrived, or the status is unrecognized.
            ResourceNotFoundError: The status is in :attr:`NOT_FOUND_CODES`.
            RateLimitError: The status is in :attr:`RATE_LIMIT_CODES`.
            ServerError: Any 5xx.
        """
        if status_code is None:
            raise ScraperError(messages.EMPTY_RESPONSE_DETAIL)
        if status_code == 200:
            return
        if status_code in self.NOT_FOUND_CODES:
            raise ResourceNotFoundError(messages.not_found_detail(status_code))
        if status_code in self.RATE_LIMIT_CODES:
            raise RateLimitError(messages.rate_limited_detail(status_code))
        if 500 <= status_code < 600:
            raise ServerError(messages.server_error_detail(status_code))
        raise ScraperError(messages.http_failed_detail(status_code))


__all__ = ["HttpScraperClient"]
