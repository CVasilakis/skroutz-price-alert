# Skroutz plugin

Tracks individual product pages on the supported Skroutz country domains. Valid product
paths contain `/s/<numeric-id>/`; search and category pages are rejected.

Rows use the shared fields (`name`, `url`, `target_price`, optional machine state). The
client resolves the product's current minimum price and returns a `PriceResult` in euros.
Row identity is the cleaned product URL.

The plugin has no custom settings. Its private `tls-client` dependency is declared in
`requirements.txt`. Copy `config.example.json` to `config/skroutz.json` before running.
