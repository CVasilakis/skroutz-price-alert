"""Import-light descriptor for the Skroutz scraper."""

import re
from urllib.parse import SplitResult

from core.scrapers.api import ScraperPlugin


def is_product_url(url: SplitResult) -> bool:
    return re.search(r"/s/\d+(?:/|$)", url.path) is not None


PLUGIN = ScraperPlugin(
    display_name="Skroutz",
    domains=("skroutz.gr", "skroutz.cy", "skroutz.ro", "skroutz.bg", "skroutz.de"),
    accepts_url=is_product_url,
    default_interval="1h",
)
