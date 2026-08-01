## Plugin contribution checklist

For a new scraper plugin, confirm that:

- [ ] The diff adds only `src/core/scrapers/plugins/<target>/` and, when included,
      `tests/plugins/<target>/`.
- [ ] `plugin.py` and `__init__.py` are import-light and use no third-party imports.
- [ ] `Client` uses bounded requests, modeled exceptions, and clean shutdown.
- [ ] Optional tests, when included, use mocked/fixture responses and cover success, malformed data,
      unavailable/no-match behavior, relevant status codes, field/setting
      codecs, URL shapes when applicable, and cleanup.
- [ ] `config.example.json` contains a valid item and demonstrates every custom key.
- [ ] The package README documents its inputs, result type, custom keys, and dependencies.
- [ ] `requirements.txt` contains only plugin-private dependencies, when needed.
- [ ] `./scripts/dev/plugin-check.sh --<target>` and
      `./scripts/dev/check.sh --debug` pass.

For non-plugin changes, remove this checklist or mark non-applicable items clearly.
