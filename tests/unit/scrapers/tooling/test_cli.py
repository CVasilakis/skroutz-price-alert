import json
from unittest import mock

from core.scrapers.framework.catalog import PluginCatalog
from core.scrapers.tooling.check import check_plugin
from core.scrapers.tooling.cli import main as cli_main
from core.scrapers.tooling.cli import resolve_schedule
from core.settings import SettingStatus


def test_schedule_missing_and_valid_config(tmp_path):
    plugin = PluginCatalog.discover().get("skroutz")
    missing = resolve_schedule(plugin, str(tmp_path))
    assert missing.status is SettingStatus.NO_CONFIG
    assert missing.on_calendar == "hourly"
    (tmp_path / "skroutz.json").write_text(
        json.dumps(
            {
                "settings": {"execution_interval": "2 hours"},
                "items": [],
            }
        )
    )
    valid = resolve_schedule(plugin, str(tmp_path))
    assert valid.status is SettingStatus.OK
    assert valid.on_calendar == "*-*-* 00/2:00:00"


def test_verifier_and_manifest_cli(capsys, tmp_path):
    assert "state round-trip" in check_plugin("skroutz")
    assert cli_main(["manifest", "--config-dir", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "skroutz\tSkroutz\t" in output and "\thourly\tnocfg" in output
    assert cli_main(["intervals"]) == 0
    assert "1h" in capsys.readouterr().out
    assert cli_main(["requirements"]) == 0
    requirements_output = capsys.readouterr().out
    assert "skroutz\t" in requirements_output
    with mock.patch("core.scrapers.tooling.cli.check_plugin", return_value=["contributor files"]):
        assert cli_main(["plugin-check", "skroutz"]) == 0
    with mock.patch(
        "core.scrapers.tooling.cli.PluginCatalog.discover", side_effect=RuntimeError("bad")
    ):
        assert cli_main(["diagnose"]) == 1
