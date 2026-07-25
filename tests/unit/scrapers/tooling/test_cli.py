import json
from unittest import mock

import pytest

from core.scrapers.framework.catalog import PluginCatalog
from core.scrapers.tooling.check import check_plugin
from core.scrapers.tooling.cli import _tsv_row, catalog_rows, resolve_schedule, schedule_rows
from core.scrapers.tooling.cli import main as cli_main
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


def test_catalog_and_schedules_are_independent(capsys, tmp_path):
    catalog = PluginCatalog.discover()
    (tmp_path / "insomnia.json").write_text(json.dumps({"products": [], "settings": {}}))

    catalog_output = catalog_rows(catalog)
    assert any(row.startswith("insomnia\tInsomnia\t") for row in catalog_output)
    schedules = schedule_rows(catalog, str(tmp_path))
    statuses = {parts[0]: parts for row in schedules if (parts := row.split("\t"))}
    assert statuses["insomnia"][1:3] == ["", "error"]
    assert "unsupported keys" in statuses["insomnia"][3]
    assert statuses["skroutz"][2] == "nocfg"


def test_verifier_and_tooling_cli(capsys, tmp_path):
    assert "state round-trip" in check_plugin("skroutz")
    assert cli_main(["catalog"]) == 0
    output = capsys.readouterr().out
    assert "skroutz\tSkroutz\t" in output
    assert cli_main(["schedules", "--config-dir", str(tmp_path)]) == 0
    assert "skroutz\thourly\tnocfg\t" in capsys.readouterr().out
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


def test_manifest_command_was_removed():
    with pytest.raises(SystemExit) as exc:
        cli_main(["manifest"])
    assert exc.value.code == 2


@pytest.mark.parametrize("value", ("embedded\ttab", "embedded\nnewline", "embedded\rreturn"))
def test_tsv_rows_reject_record_delimiters(value):
    with pytest.raises(ValueError, match="TSV field"):
        _tsv_row("target", value)
