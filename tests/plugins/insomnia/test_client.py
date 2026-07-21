from unittest import mock

import pytest

from core.exceptions import RateLimitError, ScraperParseError
from core.scrapers.api import TrackedItem
from core.scrapers.framework.catalog import PluginCatalog
from core.scrapers.plugins.insomnia.client import Client
from core.scrapers.plugins.insomnia.plugin import (
    TITLE_EXCLUDE,
    TITLE_INCLUDE,
    URL,
)
from core.settings import resolve_settings

HTML = """
<li class="insAdvertsList"><h4><a href="/classifieds/ad/1/">Pixel 9 128GB</a></h4><p class="cFilePrice">450,00 €</p></li>
<li class="insAdvertsList"><h4><a href="/classifieds/ad/2/">Pixel 9a 128GB</a></h4><p class="cFilePrice">350,00 €</p></li>
<li class="insAdvertsList"><span class="insRequest">wanted</span><h4><a href="/x">Pixel 9</a></h4><p class="cFilePrice">1 €</p></li>
<li class="insAdvertsList"><h4><a href="/x">Pixel swap</a></h4><p class="cFilePrice">Επικοινωνία</p></li>
"""


class Response:
    def __init__(self, *, text: str = "", status: int = 200):
        self.text = text
        self.status_code = status


def _client(html: str = HTML, status: int = 200, floor: float = 30) -> Client:
    plugin = PluginCatalog.discover().get("insomnia")
    settings = resolve_settings(plugin.setting_specs, {"min_advert_price": floor})
    with mock.patch("core.scrapers.support.http.tls_client.Session"):
        client = Client(settings)
    client.get = mock.Mock(return_value=Response(text=html, status=status))
    return client


def _search(include=(), exclude=()) -> TrackedItem:
    return TrackedItem(
        "id",
        "Name",
        500,
        _custom={
            URL: "https://www.insomnia.gr/classifieds/category/x/",
            TITLE_INCLUDE: tuple(include),
            TITLE_EXCLUDE: tuple(exclude),
        },
    )


def test_filters_offers_and_returns_an_empty_listing_for_no_match():
    client = _client()
    result = client.scrape(_search(("pixel", "128"), ("9a",)))
    assert [offer.title for offer in result.offers] == ["Pixel 9 128GB"]
    assert tuple(client.scrape(_search(("iphone",))).offers) == ()


def test_modeled_remote_and_parse_failures():
    with pytest.raises(ScraperParseError):
        _client("<p>blocked</p>").scrape(_search())
    with pytest.raises(ScraperParseError):
        _client('<li class="insAdvertsList"><h4><a href="/x">Ad</a></h4></li>').scrape(_search())
    with pytest.raises(RateLimitError):
        _client(status=429).scrape(_search())
