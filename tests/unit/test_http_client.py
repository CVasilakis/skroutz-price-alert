"""Unit tests for the shared HTTP scraper client.

Covers the pure HTTP-status -> modeled-exception mapping (``raise_for_status``,
the table the orchestrator's ErrorPolicy is keyed on) and identity rotation
(``refresh_identity``). ``tls_client`` is patched, so no real session or network
is involved. The module imports ``tls_client`` at top (a per-plugin transport
dep), so the whole suite skips cleanly on a core-only install.

Note: the retry/back-off loop is NOT here — it lives in the orchestrator and is
covered by test_orchestrator.py.
"""

import unittest
from unittest import mock

from core.exceptions import (
    ScraperError, RateLimitError, ServerError, ProductNotFoundError,
)

try:
    from core.scrapers.base.http_client import HttpScraperClient

    class _ConcreteClient(HttpScraperClient):
        """Minimal concrete client (HttpScraperClient.scrape_product is abstract)."""
        HEADERS_POOL = [{"User-Agent": "test"}]

        def scrape_product(self, product_url):  # never called in these tests
            raise NotImplementedError

    _HAS_TLS = True
except Exception:  # pragma: no cover - tls_client not installed (core-only install)
    _HAS_TLS = False


def _make_client():
    """Builds a concrete client with tls_client.Session patched inert."""
    with mock.patch("core.scrapers.base.http_client.tls_client.Session"):
        return _ConcreteClient()


@unittest.skipUnless(_HAS_TLS, "tls_client not installed")
class TestRaiseForStatus(unittest.TestCase):
    def setUp(self):
        self.client = _make_client()

    def test_200_does_not_raise(self):
        self.assertIsNone(self.client.raise_for_status(200))

    def test_none_status_raises_scraper_error(self):
        with self.assertRaises(ScraperError):
            self.client.raise_for_status(None)

    def test_not_found_codes(self):
        for code in (404, 410):
            with self.subTest(code=code), self.assertRaises(ProductNotFoundError):
                self.client.raise_for_status(code)

    def test_rate_limit_codes(self):
        for code in (401, 403, 429):
            with self.subTest(code=code), self.assertRaises(RateLimitError):
                self.client.raise_for_status(code)

    def test_server_error_range(self):
        for code in (500, 503, 599):
            with self.subTest(code=code), self.assertRaises(ServerError):
                self.client.raise_for_status(code)

    def test_other_codes_fall_through_to_scraper_error(self):
        for code in (302, 418):
            with self.subTest(code=code), self.assertRaises(ScraperError):
                self.client.raise_for_status(code)

    def test_subclass_can_override_code_mapping(self):
        class OddClient(_ConcreteClient):
            NOT_FOUND_CODES = (418,)  # this API signals "gone" with 418

        with mock.patch("core.scrapers.base.http_client.tls_client.Session"):
            client = OddClient()
        with self.assertRaises(ProductNotFoundError):
            client.raise_for_status(418)
        # 404 is no longer a not-found for this store -> generic ScraperError.
        with self.assertRaises(ScraperError):
            client.raise_for_status(404)


@unittest.skipUnless(_HAS_TLS, "tls_client not installed")
class TestRefreshIdentity(unittest.TestCase):
    def test_rotates_headers_and_replaces_session(self):
        s1, s2 = mock.Mock(name="session1"), mock.Mock(name="session2")
        with mock.patch("core.scrapers.base.http_client.tls_client.Session",
                        side_effect=[s1, s2]), \
             mock.patch("core.scrapers.base.http_client.random.choice",
                        return_value={"User-Agent": "probe"}):
            client = _ConcreteClient()
            self.assertIs(client.session, s1)

            client.refresh_identity()

            s1.close.assert_called_once()          # old session is closed
            self.assertIs(client.session, s2)      # a fresh session is installed
            self.assertEqual(client.current_headers, {"User-Agent": "probe"})

    def test_mutating_current_headers_never_pollutes_the_pool(self):
        # current_headers is a copy of the chosen profile, so a subclass mutating
        # it in place cannot corrupt the shared class-level pool for later
        # identities (or other instances).
        profile = {"User-Agent": "original"}

        class OneProfileClient(_ConcreteClient):
            HEADERS_POOL = [profile]

        with mock.patch("core.scrapers.base.http_client.tls_client.Session"):
            client = OneProfileClient()
            client.current_headers["authority"] = "mutated.example"

            client.refresh_identity()

            self.assertNotIn("authority", client.current_headers)
            self.assertEqual(profile, {"User-Agent": "original"})


if __name__ == "__main__":
    unittest.main()
