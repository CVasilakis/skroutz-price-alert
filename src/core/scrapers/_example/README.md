# Example scraper plugin

Copy this directory to `src/core/scrapers/<target>`. Update the descriptor, client,
example config, and this README, then run `./scripts/plugin-check.sh --<target>`.

The required files are `__init__.py`, `plugin.py`, `client.py`, `README.md`, and
`config.example.json`. Item fields, custom settings, and a private
`requirements.txt` are optional. Descriptor imports must remain stdlib-only apart
from `core.scrapers.api`, and `client.py` must export `Client`.

The leading underscore keeps this package out of automatic plugin discovery.
