# Example scraper plugin

Use `./scripts/dev/plugin-create.sh` to generate a source package and its matching test
package from this minimal shape. Update the descriptor, client, example config,
package-local guide, and generated behavior tests, then run
`./scripts/dev/plugin-check.sh --<target>`.

Runtime discovery requires `__init__.py`, `plugin.py`, and `client.py`; the contributor
verifier also requires this README, `config.example.json`, and a corresponding
`tests/plugins/<target>/test_*.py`. Item fields, custom settings, import-light helpers,
and a private `requirements.txt` are optional advanced additions documented in
`CONTRIBUTING.md`. Descriptors may use stdlib and `core.scrapers.api`; the isolated
probe rejects third-party import effects. `client.py` must export `Client`.
When a plugin-private configuration field genuinely changes representation, increment
`config_schema_version` and add an import-light `migrations.py` exporting plain, pure
`CONFIG_MIGRATIONS` callables. Retain every transition, update
`plugin_schema_version` in the example, and add
`tests/plugins/<target>/test_migrations.py`. Version-1 plugins must not contain a
`migrations.py`; framework-owned fields never migrate there.

The minimal example accepts product URLs under `/products/` and returns a
`PriceResult`. Replace both choices with the target's real page and result shape.

The leading underscore keeps this package out of automatic plugin discovery.
