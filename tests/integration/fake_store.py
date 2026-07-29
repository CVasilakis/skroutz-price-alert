"""Stdlib-only HTTP store used by end-to-end tests."""

import contextlib
import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core import messages
from core.exceptions import (
    RateLimitError,
    ResourceNotFoundError,
    ScraperError,
    ServerError,
)
from core.scrapers.api import (
    ListingResult,
    Offer,
    PriceResult,
    ScraperClient,
    ScrapeResult,
    TrackedItem,
    UrlField,
)

URL = UrlField("url", domains=("127.0.0.1",), accepts_url=lambda _url: True)


class FakeStoreClient(ScraperClient):
    def scrape(self, item: TrackedItem) -> ScrapeResult:
        try:
            with urllib.request.urlopen(item[URL], timeout=5) as response:
                status, body = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, body = exc.code, b""
        self._raise_for_status(status)
        data = json.loads(body)
        if "offers" in data:
            return ListingResult(
                data.get("currency", "EUR"),
                (Offer(offer["title"], offer["price"], offer["url"]) for offer in data["offers"]),
            )
        return PriceResult(data["price"], data.get("currency", "EUR"))

    @staticmethod
    def _raise_for_status(status: int) -> None:
        if status == 200:
            return
        if status in (404, 410):
            raise ResourceNotFoundError(messages.not_found_detail(status))
        if status in (401, 403, 429):
            raise RateLimitError(messages.rate_limited_detail(status))
        if 500 <= status < 600:
            raise ServerError(messages.server_error_detail(status))
        raise ScraperError(messages.http_failed_detail(status))


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - stdlib server callback name
        self.server.request_count += 1  # type: ignore[attr-defined]
        script = self.server.routes.get(self.path)  # type: ignore[attr-defined]
        if not script:
            self.send_error(404)
            return
        status, payload = script.pop(0) if len(script) > 1 else script[0]
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        if payload is None:
            body = b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@contextlib.contextmanager
def fake_store_server(routes):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.routes = {path: list(script) for path, script in routes.items()}
    server.request_count = 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
