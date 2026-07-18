from unittest import mock

import pytest

from core.exceptions import InvalidURLError, RateLimitError, ScraperParseError
from core.scrapers.api import TrackedItem
from core.scrapers.insomnia.client import InsomniaClient
from core.scrapers.insomnia.plugin import MIN_ADVERT_PRICE, TITLE_EXCLUDE, TITLE_INCLUDE
from core.scrapers.registry import ScraperRegistry
from core.scrapers.skroutz.client import SkroutzClient
from core.settings import resolve_settings


def _settings(target, values=None):
    plugin = ScraperRegistry.get_plugin(target)
    return resolve_settings(plugin.setting_specs, values or {})


def _item(url, custom=None):
    return TrackedItem("id", "Name", url, 500, _custom=custom or {})


class Response:
    def __init__(self, *, data=None, text="", status=200):
        self._data, self.text, self.status_code = data, text, status
    def json(self):
        return self._data


def test_skroutz_request_and_price_parsing():
    with mock.patch("core.scrapers.base.http_client.tls_client.Session"):
        client = SkroutzClient(_settings("skroutz"))
    client.get = mock.Mock(return_value=Response(data={"price_min": "1.234,56"}))
    result = client.scrape(_item("https://www.skroutz.gr/s/123/Product.html"))
    assert result.price == 1234.56
    assert client.get.call_args.args[0].endswith("/s/123/filter_products.json?")
    with pytest.raises(InvalidURLError):
        client.scrape(_item("https://www.skroutz.gr/search?q=x"))


HTML = """
<li class="insAdvertsList"><h4><a href="/classifieds/ad/1/">Pixel 9 128GB</a></h4><p class="cFilePrice">450,00 €</p></li>
<li class="insAdvertsList"><h4><a href="/classifieds/ad/2/">Pixel 9a 128GB</a></h4><p class="cFilePrice">350,00 €</p></li>
<li class="insAdvertsList"><span class="insRequest">wanted</span><h4><a href="/x">Pixel 9</a></h4><p class="cFilePrice">1 €</p></li>
<li class="insAdvertsList"><h4><a href="/x">Pixel swap</a></h4><p class="cFilePrice">Επικοινωνία</p></li>
"""


def _insomnia(html=HTML, status=200, floor=30):
    with mock.patch("core.scrapers.base.http_client.tls_client.Session"):
        client = InsomniaClient(_settings("insomnia", {"min_advert_price": floor}))
    client.get = mock.Mock(return_value=Response(text=html, status=status))
    return client


def _search(include=(), exclude=(), url="https://www.insomnia.gr/classifieds/category/x/"):
    return _item(url, {TITLE_INCLUDE: tuple(include), TITLE_EXCLUDE: tuple(exclude)})


def test_insomnia_filters_offers_and_no_match():
    client = _insomnia()
    result = client.scrape(_search(("pixel", "128"), ("9a",)))
    assert [offer.title for offer in result.offers] == ["Pixel 9 128GB"]
    assert tuple(client.scrape(_search(("iphone",))).offers) == ()


def test_insomnia_modeled_failures():
    with pytest.raises(ScraperParseError):
        _insomnia("<p>blocked</p>").scrape(_search())
    with pytest.raises(ScraperParseError):
        _insomnia('<li class="insAdvertsList"><h4><a href="/x">Ad</a></h4></li>').scrape(_search())
    with pytest.raises(InvalidURLError):
        _insomnia().scrape(_search(url="https://www.insomnia.gr/forums/x"))
    with pytest.raises(RateLimitError):
        _insomnia(status=429).scrape(_search())
