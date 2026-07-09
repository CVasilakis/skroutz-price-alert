"""A complete fake store for end-to-end tests: local HTTP server, urllib client,
JSON storage, and a plugin descriptor binding them.

Everything here runs on the stdlib only (``urllib`` against ``http.server``), so
the E2E suite works even on a core-only install where no scraper's transport
library (``tls_client``, ``selenium``, ...) is present. The client mirrors
``HttpScraperClient.raise_for_status``'s status -> modeled-exception mapping via
the same ``core.messages`` detail helpers, so the orchestrator's ErrorPolicy
table is exercised with production-identical error text.

Use :func:`fake_store_server` to serve scripted responses and
:func:`support.fake_plugin` (with :class:`FakeStoreClient` /
:class:`FakeStoreDataManager`) inside a ``support.registry_sandbox`` to register
the store. The plugin's domain must be built *after* the server binds, because
``BasePlugin.matches_url`` compares the full netloc — including the port.
"""

import contextlib
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core import messages
from core.exceptions import (
    ProductNotFoundError, RateLimitError, ScraperError, ServerError,
)
from core.scrapers.base.client import BaseScraperClient
from core.scrapers.base.model import BaseTrackedItem, ScrapeResult
from core.scrapers.base.storage import JsonProductDataManager


class FakeStoreClient(BaseScraperClient):
    """A transport-light scraper client fetching JSON prices over plain urllib."""

    def scrape_product(self, product_url: str) -> ScrapeResult:
        try:
            with urllib.request.urlopen(product_url, timeout=5) as resp:
                status, body = resp.status, resp.read()
        except urllib.error.HTTPError as e:
            status, body = e.code, b""
        self._raise_for_status(status)
        data = json.loads(body)
        return ScrapeResult(price=float(data["price"]), currency=data.get("currency", "€"))

    @staticmethod
    def _raise_for_status(status: int) -> None:
        """HttpScraperClient.raise_for_status's mapping, without the tls_client import."""
        if status == 200:
            return
        if status in (404, 410):
            raise ProductNotFoundError(messages.not_found_detail(status))
        if status in (401, 403, 429):
            raise RateLimitError(messages.rate_limited_detail(status))
        if 500 <= status < 600:
            raise ServerError(messages.server_error_detail(status))
        raise ScraperError(messages.http_failed_detail(status))


class FakeStoreDataManager(JsonProductDataManager):
    """JSON storage for the fake store; every URL path is a product page."""

    MODEL = BaseTrackedItem
    ROOT_KEY = "products"

    def _matches_product_path(self, url: str) -> bool:
        return True


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server API
        script = self.server.routes.get(self.path)  # type: ignore[attr-defined]
        if not script:
            self.send_error(404)
            return
        # Consume the script one response per request; the last entry repeats.
        status, payload = script.pop(0) if len(script) > 1 else script[0]
        body = json.dumps(payload).encode() if payload is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence the default stderr access log
        pass


@contextlib.contextmanager
def fake_store_server(routes: dict[str, list[tuple[int, dict | None]]]):
    """Serves scripted responses on a random localhost port; yields the netloc.

    Args:
        routes: ``path -> [(status, json_payload_or_None), ...]``. Each request
            consumes the next entry; the last entry repeats (so ``[(503, None),
            (200, {...})]`` fails once then succeeds forever).

    Yields:
        str: The server's netloc (``127.0.0.1:<port>``) — use it as the fake
            plugin's supported domain and in product URLs.
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.routes = {path: list(script) for path, script in routes.items()}  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
