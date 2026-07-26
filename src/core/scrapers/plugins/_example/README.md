# Example scraper plugin

Use `./scripts/dev/plugin-create.sh` to generate a source package and its matching test
package from this minimal shape. Update the descriptor, client, example config,
package-local guide, and generated behavior tests, then run
`./scripts/dev/plugin-check.sh --<target>`.

Runtime discovery requires `__init__.py`, `plugin.py`, and `client.py`; the contributor
verifier also requires this README, `config.example.json`, and a corresponding
`tests/plugins/<target>/test_*.py`. Descriptors may use stdlib and
`core.scrapers.api`; the isolated probe rejects third-party import effects.
`client.py` must export `Client`.

The minimal example accepts resource URLs under `/items/` and returns a
`PriceResult`. Replace both choices with the target's real page and result shape.
Run `./scripts/dev/plugin-check.sh --<target>`, then `./scripts/dev/check.sh`.

See `CONTRIBUTING.md` for optional custom fields, settings, listing results,
private dependencies, shared HTTP helpers, migrations, and presentation rules.

The leading underscore keeps this package out of automatic plugin discovery.
