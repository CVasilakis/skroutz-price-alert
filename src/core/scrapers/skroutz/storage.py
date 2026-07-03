import re
from urllib.parse import urlparse

from core.scrapers.base.storage import JsonProductDataManager
from core.scrapers.skroutz.model import Product


class SkroutzDataManager(JsonProductDataManager):
    """Data manager for Skroutz products.

    Inherits the entire JSON lifecycle and the supported-domain check from
    :class:`JsonProductDataManager`; only the Skroutz-specific URL-path rule
    (a ``/<product-id>/`` numeric segment) is declared here. The supported
    domains come from the injected plugin, so they are never duplicated.
    """

    MODEL = Product
    ROOT_KEY = "products"

    def _matches_product_path(self, url: str) -> bool:
        """Returns True if the URL path is a Skroutz product page (``/s/<id>``).

        The domain has already been confirmed supported by the base class, so this
        only needs to inspect the path. The rule is the same ``/s/<numeric-id>`` shape
        the client parses (:meth:`SkroutzClient.scrape_product`), so "scrapable"
        (storage) and "parseable" (client) agree: a non-product numeric URL (e.g.
        ``/c/123/foo.html``) is correctly rejected here rather than passing storage and
        then failing the client with ``InvalidURLError``.

        Args:
            url (str): A URL already confirmed to be on a supported Skroutz domain.

        Returns:
            bool: True if the path has a ``/s/<id>`` product segment.
        """
        return bool(re.search(r'/s/\d+', urlparse(url).path))
