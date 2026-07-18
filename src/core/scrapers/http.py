"""Optional reusable TLS transport for HTTP-based plugin clients."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING

import tls_client
from tls_client.response import Response

from core import messages
from core.scrapers.api import (
    ProductNotFoundError,
    RateLimitError,
    ScraperClient,
    ScraperError,
    ServerError,
)

if TYPE_CHECKING:
    from core.settings import ResolvedSettings


class HttpScraperClient(ScraperClient):
    """Bounded GET transport with identity rotation and standard status mapping."""

    HEADERS_POOL: list[dict[str, str]] = [{}]
    TLS_CLIENT_IDENTIFIER = "chrome120"
    REQUEST_TIMEOUT_SECONDS = 30
    NOT_FOUND_CODES = (404, 410)
    RATE_LIMIT_CODES = (401, 403, 429)

    def __init__(self, settings: "ResolvedSettings") -> None:
        super().__init__(settings)
        self.current_headers = dict(random.choice(self.HEADERS_POOL))
        self.session = self._new_session()

    def _new_session(self) -> tls_client.Session:
        return tls_client.Session(
            client_identifier=self.TLS_CLIENT_IDENTIFIER,  # type: ignore[arg-type]
            random_tls_extension_order=True,
        )

    def get(self, url: str, headers: dict[str, str] | None = None) -> Response:
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
        self.current_headers = dict(random.choice(self.HEADERS_POOL))
        self.session.close()
        self.session = self._new_session()

    def close(self) -> None:
        self.session.close()

    def raise_for_status(self, status_code: int | None) -> None:
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


__all__ = ["HttpScraperClient"]
