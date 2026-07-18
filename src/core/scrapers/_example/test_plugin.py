from urllib.parse import urlsplit

from .plugin import SKU, accepts_url


def test_descriptor_contract():
    assert SKU.decode(" abc ") == "abc"
    assert accepts_url(urlsplit("https://store.example/products/abc"))
