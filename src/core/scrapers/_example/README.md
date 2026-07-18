# Example scraper plugin

Copy this directory to `src/core/scrapers/<target>`. Update the descriptor, client,
example config, and this README, then run `./scripts/plugin-check.sh --<target>`.

Runtime discovery requires `__init__.py`, `plugin.py`, and `client.py`; the contributor
verifier also requires this README and `config.example.json`. Item fields, custom
settings, and a private `requirements.txt` are optional. Descriptors may use stdlib,
`core.scrapers.api`, and import-light plugin-local helpers; the isolated probe rejects
third-party import effects. `client.py` must export `Client`.

The leading underscore keeps this package out of automatic plugin discovery.
