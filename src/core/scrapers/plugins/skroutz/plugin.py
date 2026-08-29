"""Import-light descriptor for the Skroutz scraper."""

import re
from urllib.parse import SplitResult

from core.scrapers.api import ScraperPlugin, UrlField


def is_product_url(url: SplitResult) -> bool:
    """Accept only Skroutz product pages, whose path carries a numeric ``/s/<id>``.

    Deliberately narrow: the client derives that ID to build its API request, so a
    category or search URL would decode fine and then fail at scrape time. Rejecting
    it here reports the row as misconfigured instead.
    """
    return re.search(r"/s/\d+(?:/|$)", url.path) is not None


URL = UrlField(
    key="url",
    domains=("skroutz.gr", "skroutz.cy", "skroutz.ro", "skroutz.bg", "skroutz.de"),
    accepts_url=is_product_url,
)


PLUGIN = ScraperPlugin(
    display_name="Skroutz",
    item_fields=(URL,),
    reference_url=URL,
    default_interval="1h",
)
