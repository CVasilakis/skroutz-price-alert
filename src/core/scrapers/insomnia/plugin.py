import math

from core.scrapers.base.plugin import BasePlugin
from core.scrapers.base.client import BaseScraperClient
from core.scrapers.base.storage import BaseDataManager
from core.scrapers.base.settings import BASE_SETTING_SPECS, SettingSpec
from core.settings import unsupported_value_message

# The store-specific setting: adverts priced below this floor are ignored,
# filtering out bait listings (an iPhone for 1€ posted to attract clicks).
SETTING_MIN_ADVERT_PRICE = "min_advert_price"


def _normalize_min_advert_price(raw: object) -> float | int | None:
    """Validates the ``min_advert_price`` setting value.

    Accepts a finite non-negative number, written as a JSON number or a numeric
    string. Returns None for anything else (bools included — JSON ``true``
    must not read as 1€).

    Args:
        raw: The raw config value.

    Returns:
        float | int | None: The effective floor, or None when unsupported.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        try:
            value = float(raw)
        except OverflowError:
            return None
        return raw if math.isfinite(value) and raw >= 0 else None
    if isinstance(raw, str):
        try:
            value = float(raw.replace("€", "").strip())
        except ValueError:
            return None
        return value if math.isfinite(value) and value >= 0 else None
    return None


SPEC_MIN_ADVERT_PRICE = SettingSpec(
    key=SETTING_MIN_ADVERT_PRICE,
    label="Min Advert Price",
    normalize=_normalize_min_advert_price,
    display=lambda value: f"{value} €" if value else "disabled",
    warning=unsupported_value_message(SETTING_MIN_ADVERT_PRICE, "disabled"),
    default=0,
)


class InsomniaPlugin(BasePlugin):
    """Plugin descriptor for the insomnia.gr classifieds marketplace.

    This is the single source of truth for all insomnia-related metadata.
    Both the client and the storage reference this plugin's domain list
    to ensure they stay in sync.
    """

    _SUPPORTED_DOMAINS = ["insomnia.gr"]

    @staticmethod
    def get_name() -> str:
        return "insomnia"

    @staticmethod
    def get_display_name() -> str:
        return "Insomnia"

    @staticmethod
    def get_supported_domains() -> list[str]:
        return InsomniaPlugin._SUPPORTED_DOMAINS

    @staticmethod
    def get_config_filename() -> str:
        return "insomnia.json"

    @staticmethod
    def get_client_class() -> type[BaseScraperClient]:
        from core.scrapers.insomnia.client import InsomniaClient
        return InsomniaClient

    @staticmethod
    def get_storage_class() -> type[BaseDataManager]:
        from core.scrapers.insomnia.storage import InsomniaDataManager
        return InsomniaDataManager

    def get_setting_specs(self) -> list[SettingSpec]:
        return BASE_SETTING_SPECS + [SPEC_MIN_ADVERT_PRICE]
