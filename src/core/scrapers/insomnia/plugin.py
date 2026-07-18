"""Declarative descriptor and custom settings for the Insomnia scraper."""

import math

from core.scrapers.base.plugin import ClassRef, PluginDefinition
from core.scrapers.base.settings import SettingSpec
from core.settings import unsupported_value_message

SETTING_MIN_ADVERT_PRICE = "min_advert_price"


def _normalize_min_advert_price(raw: object) -> float | int | None:
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


PLUGIN = PluginDefinition(
    display_name="Insomnia",
    domains=("insomnia.gr",),
    client=ClassRef(".client", "InsomniaClient"),
    storage=ClassRef(".storage", "InsomniaDataManager"),
    setting_specs=(SPEC_MIN_ADVERT_PRICE,),
)
