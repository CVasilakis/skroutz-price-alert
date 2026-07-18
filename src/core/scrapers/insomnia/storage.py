from typing import Any
from urllib.parse import urlparse

from core.scrapers.base.storage import JsonProductDataManager
from core.scrapers.insomnia.model import (
    AdvertSearch, INCLUDE_PARAM, EXCLUDE_PARAM,
    is_valid_terms_field,
)


class InsomniaDataManager(JsonProductDataManager):
    """Data manager for insomnia classifieds searches.

    Inherits the entire JSON lifecycle from :class:`JsonProductDataManager` and
    declares the insomnia-specific pieces: the listing URL-path rule, the
    filter-terms validation, and — because several rows legitimately share one
    listing URL, differing only in their terms — the composite row identity
    used for dedup grouping and write-back merging (see the row-identity hooks
    in the base class and the helpers in :mod:`core.scrapers.insomnia.model`).
    """

    MODEL = AdvertSearch
    ROOT_KEY = "products"

    def _matches_product_path(self, url: str) -> bool:
        """Returns True if the URL path is an insomnia classifieds page.

        The domain has already been confirmed supported by the base class, so
        this only needs to inspect the path. Any ``/classifieds/`` page works —
        the scraper reads listing pages (e.g. ``/classifieds/category/174-google/``),
        not individual advert pages.

        Args:
            url (str): A URL already confirmed to be on insomnia.gr.

        Returns:
            bool: True if the path is under ``/classifieds/``.
        """
        return urlparse(url).path.startswith("/classifieds/")

    def is_valid_item(self, item: Any) -> bool:
        """Validates a row: the base checks plus well-formed filter-terms fields.

        A ``title_include``/``title_exclude`` that is present but not a list of
        strings marks the row misconfigured, surfacing it in the scraper's
        ``Config`` row instead of being silently read as "no terms".

        Args:
            item (dict): The item dictionary to validate.

        Returns:
            bool: True if the row is valid.
        """
        if not super().is_valid_item(item):
            return False
        return (is_valid_terms_field(item.get(INCLUDE_PARAM))
                and is_valid_terms_field(item.get(EXCLUDE_PARAM)))
