"""Unit tests for Skroutz request shaping and response parsing.

The inherited ``HttpScraperClient.get`` hook is mocked, so these tests enforce
that the plugin uses the shared bounded transport boundary without touching the
network. The timeout value itself is owned and tested by ``test_http_client``.
"""

import unittest
from typing import cast
from unittest import mock

import pytest

pytest.importorskip("tls_client", reason="skroutz dependencies not installed")

from core.exceptions import InvalidURLError
from core.scrapers.skroutz.client import SkroutzClient


class _FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data


def _client(data=None, status_code=200):
    """Returns a client whose inherited bounded GET hook is a canned response."""
    client = SkroutzClient()
    client.get = mock.Mock(
        return_value=_FakeResponse(
            {"price_min": "1.234,56"} if data is None else data,
            status_code,
        )
    )
    return client


class TestScrapeProduct(unittest.TestCase):
    def test_uses_bounded_get_hook_and_parses_price(self):
        client = _client()

        result = client.scrape_product(
            "https://www.skroutz.gr/s/123456/example-product.html"
        )

        self.assertEqual(result.price, 1234.56)
        self.assertEqual(result.currency, "€")
        get_mock = cast(mock.Mock, client.get)
        get_mock.assert_called_once()
        request_url = get_mock.call_args.args[0]
        headers = get_mock.call_args.kwargs["headers"]
        self.assertEqual(
            request_url,
            "https://www.skroutz.gr/s/123456/filter_products.json?",
        )
        self.assertEqual(headers["authority"], "www.skroutz.gr")
        self.assertEqual(
            headers["referer"].split("/", 3)[:3],
            ["https:", "", "www.skroutz.gr"],
        )

    def test_invalid_product_url_is_rejected_before_request(self):
        client = _client()

        with self.assertRaises(InvalidURLError):
            client.scrape_product("https://www.skroutz.gr/search?keyphrase=phone")

        get_mock = cast(mock.Mock, client.get)
        get_mock.assert_not_called()

if __name__ == "__main__":
    unittest.main()
