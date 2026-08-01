from unittest import mock

import pytest
from support import decode_test_config

from core.scrapers.api import (
    PriceUnavailableError,
    RateLimitError,
    ResourceNotFoundError,
    ScraperError,
    ScraperParseError,
    ServerError,
    TrackedItem,
)
from core.scrapers.plugins.skroutz.client import Client
from core.scrapers.plugins.skroutz.plugin import PLUGIN, is_product_url


class Response:
    def __init__(self, payload=None, status_code=200, error=None):
        self.payload = {"price_min": "1.234,56"} if payload is None else payload
        self.status_code = status_code
        self.error = error

    def json(self):
        if self.error is not None:
            raise self.error
        return self.payload


def _item(url: str) -> TrackedItem:
    values = decode_test_config(
        PLUGIN,
        "skroutz",
        items=[{"id": "id", "name": "Name", "target_price": 500, "url": url}],
    )
    return values.items[0]


def _client(response=None):
    values = decode_test_config(PLUGIN, "skroutz")
    with mock.patch("core.scrapers.support.http.tls_client.Session"):
        client = Client(values.settings)
    client.get = mock.Mock(return_value=response or Response())
    return client


def test_request_and_price_parsing():
    client = _client()

    result = client.scrape(_item("https://www.skroutz.gr/s/123/Product.html"))

    assert result.price == 1234.56
    assert client.get.call_args.args[0].endswith("/s/123/filter_products.json?")


def test_product_url_shape_is_explicit():
    from urllib.parse import urlsplit

    assert is_product_url(urlsplit("https://www.skroutz.gr/s/123/Product.html"))
    assert not is_product_url(urlsplit("https://www.skroutz.gr/search?q=x"))


def test_domain_specific_headers_and_currency():
    client = _client()
    result = client.scrape(_item("https://www.skroutz.ro/s/123/Product.html"))

    assert result.currency == "Lei"
    headers = client.get.call_args.kwargs["headers"]
    assert headers["authority"] == "www.skroutz.ro"
    assert headers["referer"].startswith("https://www.skroutz.ro/")


def test_unavailable_and_unparseable_prices_are_modeled():
    with pytest.raises(PriceUnavailableError):
        _client(Response({"price_min": None})).scrape(
            _item("https://www.skroutz.gr/s/123/Product.html")
        )
    with pytest.raises(ScraperParseError, match="Could not parse price"):
        _client(Response({"price_min": "contact us"})).scrape(
            _item("https://www.skroutz.gr/s/123/Product.html")
        )


def test_malformed_json_is_a_parse_failure():
    import json

    error = json.JSONDecodeError("bad", "{", 0)
    with pytest.raises(ScraperParseError, match="No JSON response"):
        _client(Response(error=error)).scrape(_item("https://www.skroutz.gr/s/123/Product.html"))


@pytest.mark.parametrize(
    "status,error_type",
    [
        (404, ResourceNotFoundError),
        (410, ResourceNotFoundError),
        (401, RateLimitError),
        (403, RateLimitError),
        (429, RateLimitError),
        (500, ServerError),
        (503, ServerError),
        (418, ScraperError),
    ],
)
def test_http_statuses_use_modeled_error_policy(status, error_type):
    with pytest.raises(error_type):
        _client(Response(status_code=status)).scrape(
            _item("https://www.skroutz.gr/s/123/Product.html")
        )
