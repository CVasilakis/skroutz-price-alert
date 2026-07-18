import json

from core.preflight import load_targets
from core.scrapers.registry import ScraperRegistry


def _write(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document))


def test_preflight_success_primes_settings_and_items(tmp_path):
    _write(tmp_path / "config" / "skroutz.json", {
        "settings": {"log_retention_days": 3},
        "items": [{
            "id": "one", "name": "One", "url": "https://skroutz.gr/s/1/One.html",
            "target_price": 1,
        }],
    })
    registry = ScraperRegistry(str(tmp_path / "config"), str(tmp_path / "state"))
    load = load_targets(registry, ["unknown", "skroutz"])[0]
    assert load.count == 1 and load.error is None
    assert registry.settings_for("skroutz") is load.settings


def test_preflight_distinguishes_config_and_state_failures(tmp_path):
    registry = ScraperRegistry(str(tmp_path / "config"), str(tmp_path / "state"))
    config_failure = load_targets(registry, ["skroutz"])[0]
    assert config_failure.error and not config_failure.state_error
    _write(tmp_path / "config" / "skroutz.json", {
        "settings": {}, "items": [],
    })
    _write(tmp_path / "state" / "skroutz.json", {"schema_version": 1, "items": []})
    registry = ScraperRegistry(str(tmp_path / "config"), str(tmp_path / "state"))
    state_failure = load_targets(registry, ["skroutz"])[0]
    assert state_failure.error and state_failure.state_error
