"""Wiring test for ``run``: the liveness reminder is checked once per invocation and
*before* the scraping orchestrator, so an aborted scrape can never suppress the heartbeat.

The background-mode tests replace what main() touches with *autospecced* doubles, so this
asserts the wiring (that the reminder is constructed and run, in the right order) *and*
that every constructor/function call still matches the real signatures — a parameter added
to ``ScrapingOrchestrator``, ``ReminderService``, ``AppriseNotifier``, ``load_target_configs``
or ``validate_notification_preflight`` fails here instead of passing silently. It does not
test any scraping behavior.

Doubles are installed with pytest's ``monkeypatch`` (the suite's usual seam) rather than a
stack of ``mock.patch`` context managers, which keeps each test flat: a nested ``with``
group needs one block per collaborator, and CPython compiles at most 20 nested blocks per
function — a ceiling the interactive test below was one collaborator away from reaching.
"""

import sys
from unittest import mock

import pytest

import core.run
from core.infrastructure.updates import SoftwareVersionStatus


def _autospec(monkeypatch, name, **kwargs):
    """Replace ``core.run.<name>`` with a signature-checked double, and return it.

    The ``monkeypatch`` equivalent of ``mock.patch(..., autospec=True)``: calls that no
    longer match the real signature raise here rather than passing silently.
    """
    double = mock.create_autospec(getattr(core.run, name), **kwargs)
    monkeypatch.setattr(core.run, name, double)
    return double


def test_reminder_runs_once_before_the_orchestrator(monkeypatch):
    order = []

    monkeypatch.setattr(sys, "argv", ["run", "--quiet"])
    _autospec(monkeypatch, "setup_global_logging")
    _autospec(monkeypatch, "ClientLoader")
    _autospec(monkeypatch, "load_target_configs", return_value=[])
    _autospec(monkeypatch, "record_general_diagnostic", side_effect=lambda general: general)
    _autospec(monkeypatch, "validate_notification_preflight", return_value=None)
    Catalog = _autospec(monkeypatch, "PluginCatalog")
    load_general = _autospec(monkeypatch, "load_general_config")
    notifier_type = _autospec(monkeypatch, "AppriseNotifier")
    StateRepository = _autospec(monkeypatch, "ReminderStateRepository")
    LockManager = _autospec(monkeypatch, "StateLockManager")
    ReminderService = _autospec(monkeypatch, "ReminderService")
    Orchestrator = _autospec(monkeypatch, "ScrapingOrchestrator")

    catalog = Catalog.discover.return_value
    catalog.targets = ("skroutz",)
    general = load_general.return_value
    general.notifications.valid_urls = ("json://localhost",)
    general.settings_error = None
    reminder = ReminderService.return_value
    reminder.run_once.side_effect = lambda: order.append("reminder")
    orchestrator = Orchestrator.return_value
    orchestrator.run.side_effect = lambda: (order.append("orchestrator"), 0)[1]

    with pytest.raises(SystemExit) as caught:
        core.run.main()

    assert caught.value.code == 0
    reminder.run_once.assert_called_once()
    assert order == ["reminder", "orchestrator"]
    load_general.assert_called_once_with(core.run.CONFIG_DIR)
    assert ReminderService.call_args.args[2] == notifier_type.return_value
    assert ReminderService.call_args.args[1] == StateRepository.return_value
    LockManager.assert_called_once_with(core.run.STATE_DIR)
    assert ReminderService.call_args.kwargs["acquire_lock_fn"] is LockManager.return_value.acquire
    notifier_type.assert_called_once_with(("json://localhost",))


def test_reminder_not_run_when_preflight_aborts(monkeypatch):
    # A fatal preflight (e.g. missing notifications in service mode) exits before the
    # reminder/orchestrator phase, so no heartbeat is attempted on an unusable config.
    failed_load = mock.MagicMock()
    failed_load.target = "skroutz"
    failed_load.settings.__getitem__.return_value = 7

    monkeypatch.setattr(sys, "argv", ["run", "--quiet"])
    _autospec(monkeypatch, "setup_global_logging")
    _autospec(monkeypatch, "ClientLoader")
    _autospec(monkeypatch, "load_target_configs", return_value=[failed_load])
    _autospec(monkeypatch, "load_general_config")
    _autospec(monkeypatch, "record_general_diagnostic", side_effect=lambda general: general)
    _autospec(monkeypatch, "validate_notification_preflight", return_value=3)
    _autospec(monkeypatch, "AppriseNotifier")
    _autospec(monkeypatch, "ReminderStateRepository")
    Catalog = _autospec(monkeypatch, "PluginCatalog")
    record_diagnostic = _autospec(monkeypatch, "record_target_load_diagnostic")
    ReminderService = _autospec(monkeypatch, "ReminderService")
    Orchestrator = _autospec(monkeypatch, "ScrapingOrchestrator")

    Catalog.discover.return_value.targets = ("skroutz",)

    with pytest.raises(SystemExit) as caught:
        core.run.main()

    assert caught.value.code == 3
    record_diagnostic.assert_called_once_with(failed_load)
    ReminderService.return_value.run_once.assert_not_called()
    Orchestrator.assert_not_called()


def test_interactive_mode_installs_handler_and_uses_interactive_strategy(monkeypatch):
    # Deliberately unspecced: this test drives the Rich/TUI collaborators, whose doubles
    # stand in for a live console rather than pinning signatures the way the two
    # background-mode tests above do.
    version_status = SoftwareVersionStatus("1.7.0", False)
    Catalog = mock.MagicMock()
    ClientLoader = mock.MagicMock()
    load_target_configs = mock.MagicMock(return_value=[])
    load_general = mock.MagicMock()
    install_handler = mock.MagicMock()
    render_config = mock.MagicMock()
    Console = mock.MagicMock()
    reporter_type = mock.MagicMock()
    Orchestrator = mock.MagicMock()

    monkeypatch.setattr(sys, "argv", ["run", "--skroutz"])
    monkeypatch.setattr(core.run, "setup_global_logging", lambda _quiet: None)
    monkeypatch.setattr(core.run, "PluginCatalog", Catalog)
    monkeypatch.setattr(core.run, "ClientLoader", ClientLoader)
    monkeypatch.setattr(core.run, "load_target_configs", load_target_configs)
    monkeypatch.setattr(core.run, "load_general_config", load_general)
    monkeypatch.setattr(core.run, "record_general_diagnostic", lambda general: general)
    monkeypatch.setattr(core.run, "install_interrupt_handler", install_handler)
    monkeypatch.setattr(core.run, "inspect_software_version", lambda: version_status)
    monkeypatch.setattr(core.run, "inspect_user_lingering", lambda: True)
    monkeypatch.setattr(core.run, "render_config_panel", render_config)
    monkeypatch.setattr(core.run, "Console", Console)
    monkeypatch.setattr(core.run.signal, "signal", lambda *_: None)
    monkeypatch.setattr(core.run, "InteractiveRunReporter", reporter_type)
    monkeypatch.setattr(core.run, "AppriseNotifier", mock.MagicMock())
    monkeypatch.setattr(core.run, "ReminderStateRepository", mock.MagicMock())
    monkeypatch.setattr(core.run, "ReminderService", mock.MagicMock())
    monkeypatch.setattr(core.run, "ScrapingOrchestrator", Orchestrator)

    plugin = mock.MagicMock(display_name="Skroutz")
    catalog = Catalog.discover.return_value
    catalog.targets = ("skroutz", "insomnia")
    catalog.get.return_value = plugin
    load_general.return_value.notifications.valid_urls = ("json://localhost",)
    Orchestrator.return_value.run.return_value = 0

    with pytest.raises(SystemExit) as caught:
        core.run.main()

    assert caught.value.code == 0
    load_target_configs.assert_called_once_with([plugin], core.run.CONFIG_DIR)
    render_config.assert_called_once_with(
        Console.return_value, load_general.return_value, version_status, True
    )
    install_handler.assert_called_once()
    Orchestrator.assert_called_once_with(
        [],
        ClientLoader.return_value,
        mock.ANY,
        False,
        reporter_type.return_value,
        state_dir=core.run.STATE_DIR,
    )


def test_debug_mode_selects_the_file_frontend_without_the_background_contract(monkeypatch):
    # --debug is a frontend choice, not a run-policy change: it drives SilentRunReporter
    # (the background log lines) to the console while keeping the interactive preflight,
    # and it must not adopt the quiet path's notification gating. Passing quiet=False to
    # the orchestrator is what keeps the target logger propagating to the console rather
    # than opening logs/<target>/output.log, so a --debug run leaves that file untouched.
    version_status = SoftwareVersionStatus("1.7.0", False)
    Catalog = mock.MagicMock()
    ClientLoader = mock.MagicMock()
    load_target_configs = mock.MagicMock(return_value=[])
    load_general = mock.MagicMock()
    render_config = mock.MagicMock()
    preflight = mock.MagicMock()
    silent_type = mock.MagicMock()
    interactive_type = mock.MagicMock()
    Orchestrator = mock.MagicMock()

    monkeypatch.setattr(sys, "argv", ["run", "--debug", "--skroutz"])
    monkeypatch.setattr(core.run, "setup_global_logging", lambda _quiet: None)
    monkeypatch.setattr(core.run, "PluginCatalog", Catalog)
    monkeypatch.setattr(core.run, "ClientLoader", ClientLoader)
    monkeypatch.setattr(core.run, "load_target_configs", load_target_configs)
    monkeypatch.setattr(core.run, "load_general_config", load_general)
    monkeypatch.setattr(core.run, "record_general_diagnostic", lambda general: general)
    monkeypatch.setattr(core.run, "validate_notification_preflight", preflight)
    monkeypatch.setattr(core.run, "install_interrupt_handler", mock.MagicMock())
    monkeypatch.setattr(core.run, "inspect_software_version", lambda: version_status)
    monkeypatch.setattr(core.run, "inspect_user_lingering", lambda: True)
    monkeypatch.setattr(core.run, "render_config_panel", render_config)
    monkeypatch.setattr(core.run, "Console", mock.MagicMock())
    monkeypatch.setattr(core.run.signal, "signal", lambda *_: None)
    monkeypatch.setattr(core.run, "SilentRunReporter", silent_type)
    monkeypatch.setattr(core.run, "InteractiveRunReporter", interactive_type)
    monkeypatch.setattr(core.run, "AppriseNotifier", mock.MagicMock())
    monkeypatch.setattr(core.run, "ReminderStateRepository", mock.MagicMock())
    monkeypatch.setattr(core.run, "ReminderService", mock.MagicMock())
    monkeypatch.setattr(core.run, "ScrapingOrchestrator", Orchestrator)

    catalog = Catalog.discover.return_value
    catalog.targets = ("skroutz", "insomnia")
    catalog.get.return_value = mock.MagicMock(display_name="Skroutz")
    load_general.return_value.notifications.valid_urls = ("json://localhost",)
    Orchestrator.return_value.run.return_value = 0

    with pytest.raises(SystemExit) as caught:
        core.run.main()

    assert caught.value.code == 0
    interactive_type.assert_not_called()
    preflight.assert_not_called()
    render_config.assert_called_once()
    Orchestrator.assert_called_once_with(
        [],
        ClientLoader.return_value,
        mock.ANY,
        False,
        silent_type.return_value,
        state_dir=core.run.STATE_DIR,
    )


def test_quiet_and_debug_are_mutually_exclusive(monkeypatch):
    # run.sh rejects the pair in the project's wording; this is the direct-invocation
    # guard behind it, so neither frontend can be selected twice over.
    Catalog = mock.MagicMock()
    Catalog.discover.return_value.targets = ()

    monkeypatch.setattr(sys, "argv", ["run", "--quiet", "--debug"])
    monkeypatch.setattr(core.run, "setup_global_logging", lambda _quiet: None)
    monkeypatch.setattr(core.run, "PluginCatalog", Catalog)

    with pytest.raises(SystemExit) as caught:
        core.run.main()

    assert caught.value.code == 2
