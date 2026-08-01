from unittest import mock

import pytest
from support import decode_test_config

from core.scrapers.api import RateLimitError, ScraperParseError, TrackedItem
from core.scrapers.plugins.insomnia.client import Client
from core.scrapers.plugins.insomnia.plugin import PLUGIN

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
    values = decode_test_config(
        PLUGIN,
        "insomnia",
        settings={"min_advert_price": floor},
    )
    with mock.patch("core.scrapers.support.http.tls_client.Session"):
        client = Client(values.settings)
    client.get = mock.Mock(return_value=Response(text=html, status=status))
    return client


def _search(include=(), exclude=()) -> TrackedItem:
    values = decode_test_config(
        PLUGIN,
        "insomnia",
        items=[
            {
                "id": "id",
                "name": "Name",
                "target_price": 500,
                "url": "https://www.insomnia.gr/classifieds/category/x/",
                "title_include": list(include),
                "title_exclude": list(exclude),
            }
        ],
    )
    return values.items[0]


def test_filters_offers_and_returns_an_empty_listing_for_no_match():
    client = _client()
    result = client.scrape(_search(("pixel", "128"), ("9a",)))
    assert [offer.title for offer in result.offers] == ["Pixel 9 128GB"]
    assert result.offers[0].url == "https://www.insomnia.gr/classifieds/ad/1/"
    assert tuple(client.scrape(_search(("iphone",))).offers) == ()


def test_absolute_advert_link_is_preserved():
    html = """
    <li class="insAdvertsList">
      <h4><a href="https://cdn.example/classifieds/ad/1?x=1#fragment">Pixel</a></h4>
      <p class="cFilePrice">100 €</p>
    </li>
    """
    result = _client(html).scrape(_search())
    assert result.offers[0].url == "https://cdn.example/classifieds/ad/1?x=1"


@pytest.mark.parametrize(
    "anchor",
    [
        "<a>Pixel</a>",
        '<a href="">Pixel</a>',
        '<a href="   ">Pixel</a>',
        '<a href="javascript:alert(1)">Pixel</a>',
        '<a href="ftp://example.com/ad/1">Pixel</a>',
        '<a href="https://user:secret@example.com/ad/1">Pixel</a>',
    ],
)
def test_invalid_advert_links_are_parse_failures(anchor):
    html = '<li class="insAdvertsList"><h4>' + anchor + '</h4><p class="cFilePrice">100 €</p></li>'
    with pytest.raises(ScraperParseError, match="URL|link"):
        _client(html).scrape(_search())


def test_blank_advert_title_is_a_title_specific_parse_failure():
    html = """
    <li class="insAdvertsList">
      <h4><a href="/classifieds/ad/1/">   </a></h4>
      <p class="cFilePrice">100 €</p>
    </li>
    """
    with pytest.raises(ScraperParseError) as raised:
        _client(html).scrape(_search())

    assert "title" in str(raised.value).casefold()
    assert "url" not in str(raised.value).casefold()


def test_minimum_price_floor_filters_only_implausibly_cheap_adverts():
    result = _client(floor=400).scrape(_search())
    assert [offer.price for offer in result.offers] == [450.0]
    result_without_floor = _client(floor=0).scrape(_search())
    assert [offer.price for offer in result_without_floor.offers] == [350.0, 450.0]


def test_modeled_remote_and_parse_failures():
    with pytest.raises(ScraperParseError):
        _client("<p>blocked</p>").scrape(_search())
    with pytest.raises(ScraperParseError):
        _client('<li class="insAdvertsList"><h4><a href="/x">Ad</a></h4></li>').scrape(_search())
    with pytest.raises(RateLimitError):
        _client(status=429).scrape(_search())
