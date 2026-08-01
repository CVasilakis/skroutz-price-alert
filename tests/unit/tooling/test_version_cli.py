from unittest import mock

from core.tooling import version_cli


def test_known_version_is_printed(capsys):
    with mock.patch.object(version_cli, "local_software_version", return_value="1.2.3"):
        assert version_cli.main() == 0

    assert capsys.readouterr().out == "1.2.3\n"


def test_unknown_version_exits_successfully_without_output(capsys):
    with mock.patch.object(version_cli, "local_software_version", return_value=None):
        assert version_cli.main() == 0

    assert capsys.readouterr().out == ""
