import pytest
from support import decode_test_config

from core.scrapers.api import ItemField, ScraperPlugin, SettingSpec, UrlField


def _string_tuple(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, (list, tuple)) or any(not isinstance(value, str) for value in raw):
        raise ValueError("must be an array of strings")
    return tuple(value.strip() for value in raw if value.strip())


def _nonblank(raw: object) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("must be a nonblank string")
    return raw.strip()


URL = UrlField(
    "url",
    domains=("store.example",),
    accepts_url=lambda url: url.path.startswith("/items/"),
)
TAGS = ItemField("tags", _string_tuple, default=())
REGION = SettingSpec("region", _nonblank, default="global")
TOKEN = SettingSpec("token", _nonblank, sensitive=True)
PLUGIN = ScraperPlugin(
    display_name="Test Store",
    item_fields=(URL, TAGS),
    settings=(REGION, TOKEN),
    reference_url=URL,
)


def test_decode_test_config_uses_runtime_codecs_defaults_and_url_canonicalization():
    values = decode_test_config(
        PLUGIN,
        "test_store",
        settings={"region": " eu ", "token": " secret "},
        items=[
            {
                "id": "one",
                "name": "One",
                "target_price": 10,
                "url": "HTTPS://STORE.EXAMPLE/items/one?variant=blue#fragment",
            }
        ],
    )

    assert values.settings[REGION] == "eu"
    assert values.settings[TOKEN] == "secret"
    assert values.items[0][URL] == "https://STORE.EXAMPLE/items/one?variant=blue"
    assert values.items[0][TAGS] == ()


def test_decode_test_config_reports_required_settings_and_indexed_item_failures():
    with pytest.raises(ValueError, match="required settings.*token"):
        decode_test_config(PLUGIN, "test_store")

    with pytest.raises(ValueError, match="item 1: url: URL path is not accepted"):
        decode_test_config(
            PLUGIN,
            "test_store",
            settings={"token": "secret"},
            items=[
                {
                    "id": "one",
                    "name": "One",
                    "target_price": 10,
                    "url": "https://store.example/search",
                }
            ],
        )
