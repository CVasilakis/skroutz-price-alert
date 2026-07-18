# Example scraper plugin

Copy this directory to `src/core/scrapers/<target>`. Update the descriptor, client,
example config, and this README, then run `./scripts/plugin-check.sh --<target>`.

Only `plugin.py` and `client.py` contain implementation; keep the import-light package
marker. Item fields, custom settings, and a private `requirements.txt` are optional.
Descriptor imports must remain stdlib-only apart from `core.scrapers.api`.

The leading underscore keeps this package out of automatic plugin discovery.
