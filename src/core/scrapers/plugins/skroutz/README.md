# Skroutz plugin

Tracks individual product pages on the supported Skroutz country domains. Valid product
paths contain `/s/<numeric-id>/`; search and category pages are rejected.

Rows use the shared fields (`id`, `name`, `url`, `target_price`, and optional
`skip`). The client resolves the product's current minimum price and returns a
`PriceResult`. State is framework-owned in `state/skroutz.json` and keyed only by `id`.

`client.py` exports the conventional `Client` and uses the shared
`core.scrapers.support.http.HttpScraperClient` transport plus
`core.scrapers.support.pricing.parse_price`. The plugin has no custom settings. Its private
`tls-client` dependency is declared in `requirements.txt`. Copy
`config.example.json` to `config/skroutz.json` before running.
