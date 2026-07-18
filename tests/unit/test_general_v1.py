import json
from datetime import datetime, timedelta
from unittest import mock

import pytest

from core.exceptions import ConfigFileError
from core.general.reminder import ReminderService, most_recent_slot, next_due_slot
from core.general.settings import (
    SPEC_REMINDER, SPEC_REMINDER_DAY, SPEC_REMINDER_TIME, resolve_general_settings,
)
from core.settings import SettingStatus


def _config(root, settings):
    path = root / "config" / "general.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"settings": settings}))
    return path


def _notifier(delivered=True):
    notifier = mock.Mock()
    notifier.has_services = True
    notifier.notify_reminder.return_value = delivered
    return notifier


def test_general_config_is_strict_and_typed(tmp_path):
    config_dir = tmp_path / "config"
    _config(tmp_path, {"reminder": "1 week", "reminder_day": "monday", "reminder_time": "08:30"})
    resolved = resolve_general_settings(str(config_dir))
    assert resolved[SPEC_REMINDER] == "1w"
    assert resolved[SPEC_REMINDER_DAY] == "Monday"
    assert resolved[SPEC_REMINDER_TIME] == "08:30"
    assert resolved.status(SPEC_REMINDER) is SettingStatus.OK
    _config(tmp_path, {"unknown": 1})
    with pytest.raises(ConfigFileError):
        resolve_general_settings(str(config_dir))


@pytest.mark.parametrize("document, message", [
    ([], "must contain an object"),
    ({"schema_version": 1, "settings": {}}, "Unknown general config keys"),
    ({"settings": [],}, "General settings must be an object"),
    ({"settings": {}, "metadata": {}}, "Unknown general config keys"),
])
def test_general_config_rejects_malformed_and_unknown_fields(tmp_path, document, message):
    path = tmp_path / "config" / "general.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(document))
    with pytest.raises(ConfigFileError, match=message):
        resolve_general_settings(str(path.parent))


def test_missing_general_config_uses_defaults(tmp_path):
    resolved = resolve_general_settings(str(tmp_path / "config"))
    assert resolved.status(SPEC_REMINDER) is SettingStatus.NO_CONFIG


def test_reminder_initializes_separate_state_then_delivers_when_due(tmp_path):
    _config(tmp_path, {"reminder": "1 week", "reminder_day": "Saturday", "reminder_time": "13:00"})
    now = datetime(2026, 7, 18, 14, 0)
    notifier = _notifier()
    service = ReminderService(str(tmp_path / "config"), notifier,
                              now_fn=lambda: now, update_check_fn=lambda: False)
    with mock.patch("core.general.reminder.acquire_lock", return_value=mock.MagicMock()):
        service.run_once()
    state_path = tmp_path / "state" / "general.json"
    assert state_path.exists()
    notifier.notify_reminder.assert_not_called()
    _, slot, problem = service._read_state()
    assert problem is None and slot == most_recent_slot(now)

    service._now_fn = lambda: now + timedelta(days=7, minutes=1)
    with mock.patch("core.general.reminder.acquire_lock", return_value=mock.MagicMock()):
        service.run_once()
    notifier.notify_reminder.assert_called_once()
    _, advanced, _ = service._read_state()
    assert advanced == next_due_slot(slot, 1)


def test_failed_delivery_does_not_advance_and_off_creates_no_state(tmp_path):
    _config(tmp_path, {"reminder": "off"})
    notifier = _notifier(False)
    service = ReminderService(str(tmp_path / "config"), notifier,
                              now_fn=lambda: datetime(2026, 7, 18, 14))
    service.run_once()
    assert not (tmp_path / "state" / "general.json").exists()
    notifier.notify_reminder.assert_not_called()


def test_corrupt_general_state_is_preserved(tmp_path):
    _config(tmp_path, {"reminder": "1 week"})
    path = tmp_path / "state" / "general.json"
    path.parent.mkdir()
    path.write_text("{broken")
    original = path.read_bytes()
    service = ReminderService(str(tmp_path / "config"), _notifier(),
                              now_fn=lambda: datetime(2026, 7, 18, 14))
    service.run_once()
    assert path.read_bytes() == original


def test_general_state_requires_internal_schema_version(tmp_path):
    _config(tmp_path, {"reminder": "1 week"})
    path = tmp_path / "state" / "general.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"last_reminder": "2026-07-18T11:00:00Z"}))
    service = ReminderService(str(tmp_path / "config"), _notifier())
    data, slot, problem = service._read_state()
    assert data is None and slot is None and problem == "corrupt"


def test_no_services_update_failure_and_persist_failure_are_nonfatal(tmp_path):
    _config(tmp_path, {"reminder": "1 week"})
    notifier = _notifier()
    notifier.has_services = False
    service = ReminderService(str(tmp_path / "config"), notifier,
                              now_fn=lambda: datetime(2026, 7, 18, 14))
    service._logger = mock.Mock()
    service.run_once()
    notifier.notify_reminder.assert_not_called()
    service._update_check_fn = mock.Mock(side_effect=RuntimeError("offline"))
    assert service._check_updates() is None
    with mock.patch("core.general.reminder.write_json_atomically", side_effect=OSError("full")):
        assert not service._persist_slot({}, datetime(2026, 7, 18, 13))


def test_due_delivery_exception_leaves_existing_slot(tmp_path):
    _config(tmp_path, {"reminder": "1 week"})
    start = datetime(2026, 7, 18, 14)
    notifier = _notifier()
    service = ReminderService(str(tmp_path / "config"), notifier,
                              now_fn=lambda: start, update_check_fn=lambda: False)
    service._logger = mock.Mock()
    assert service._persist_slot({}, most_recent_slot(start))
    before = (tmp_path / "state" / "general.json").read_bytes()
    service._now_fn = lambda: start + timedelta(days=7, minutes=1)
    notifier.notify_reminder.side_effect = RuntimeError("transport")
    service._check_and_send(1, "1w", 5, 13, 0)
    assert (tmp_path / "state" / "general.json").read_bytes() == before
