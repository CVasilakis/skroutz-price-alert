from dataclasses import dataclass
from core.scrapers.base.model import BaseTrackedItem


@dataclass
class Product(BaseTrackedItem):
    """Represents a product tracked on Skroutz.

    Currently inherits all fields from ``BaseTrackedItem`` without
    additions.  Exists as a dedicated type so that Skroutz-specific
    fields (e.g. ``sku``, ``category``) can be added here in the
    future without modifying the base class.
    """
    pass
