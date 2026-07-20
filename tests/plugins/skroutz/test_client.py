from unittest import mock

import pytest

from core.exceptions import InvalidURLError
from core.scrapers.api import TrackedItem
from core.scrapers.registry import PluginCatalog
from core.scrapers.skroutz.client import Client
from core.scrapers.skroutz.plugin import URL
from core.settings import resolve_settings


class Response:
    status_code = 200

    def json(self):
        return {"price_min": "1.234,56"}


def _item(url: str) -> TrackedItem:
    return TrackedItem("id", "Name", 500, _custom={URL: url})


def test_request_and_price_parsing_keeps_defensive_id_extraction():
    plugin = PluginCatalog.discover().get("skroutz")
    with mock.patch("core.scrapers.http.tls_client.Session"):
        client = Client(resolve_settings(plugin.setting_specs, {}))
    client.get = mock.Mock(return_value=Response())

    result = client.scrape(_item("https://www.skroutz.gr/s/123/Product.html"))

    assert result.price == 1234.56
    assert client.get.call_args.args[0].endswith("/s/123/filter_products.json?")
    with pytest.raises(InvalidURLError):
        client.scrape(_item("https://www.skroutz.gr/search?q=x"))
