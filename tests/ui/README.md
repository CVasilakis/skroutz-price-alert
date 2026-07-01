# Terminal-UI test suite

Automated coverage for everything the app renders to the terminal: the interactive
scraping panel (a standard run), and the `--status`, `--ping`, and Configuration Check
panels. One **scenario catalog** is the single source of truth, consumed by two things:

- **Snapshot gate** (`test_ui_snapshots.py`) — renders each scenario to plain text and
  compares it against a committed golden file in `snapshots/`. Catches layout/wrapping
  changes; the golden file's first line (`# border: <color>`) catches border-color
  changes as a one-line diff.
- **Color asserts** (`test_ui_colors.py`) — assert the border-color *decision* per
  surface (valid for all scenarios; a specific expected color for a representative set).
- **Gallery** (`gallery.py`) — renders the same scenarios in full color for eyeballing.

The panels are rendered by driving the *real* production code with synthetic inputs (no
network, no systemd, no Wi-Fi toggling), so a snapshot reflects exactly what ships.

## Layout

```
tests/ui/
  catalog/                 # the single source of truth
    _base.py               # Scenario, Surface, BuildResult, the @scenario registrar
    inputs.py              # shared input builders (SettingViews, ResolvedSettings, systemd dicts, TargetLoad)
    run_scenarios.py       # interactive scraping-panel cases
    status_scenarios.py    # --status: service / not-installed / orphan
    ping_scenarios.py      # --ping
    config_scenarios.py    # Configuration Check
  harness/
    drivers.py             # drive_run / drive_service / drive_ping / drive_config ...
    rendering.py           # recording console + capture (deterministic)
  snapshots/               # golden files: <surface>__<name>.txt
  test_ui_snapshots.py
  test_ui_colors.py
  gallery.py
```

## Running

```sh
# Run the whole test suite (existing tests + UI snapshots + colors)
PYTHONPATH=src/core ./venv/bin/python3 -m unittest discover -s tests

# (Re)generate the golden snapshots after an intended UI change — review the diff first
UPDATE_SNAPSHOTS=1 PYTHONPATH=src/core ./venv/bin/python3 -m unittest discover -s tests

# Eyeball every panel in color
./venv/bin/python3 tests/ui/gallery.py
./venv/bin/python3 tests/ui/gallery.py --surface status     # one surface
./venv/bin/python3 tests/ui/gallery.py --tag interrupt      # one tag
./venv/bin/python3 tests/ui/gallery.py --html /tmp/ui.html  # shareable page
./venv/bin/python3 tests/ui/gallery.py --list               # list scenario keys
```

## Add / modify / remove a scenario

**Add** — drop one decorated function into the matching `catalog/*_scenarios.py`:

```python
@scenario(Surface.RUN, "my_case", "One-line description", tags=("price",))
def _():
    def script(s):
        _start(s)
        s.start_scraping("Widget", 1, 3); s.complete_scraping()
        s.log_price_result("Widget", 9.99, CURRENCY, 12.0, PriceOutcome.DROP, notes=[NOTIFIED_OK])
        s.complete_target()   # end a finished panel here; omit for a mid-flight capture
    return drive_run(script)
```

Then mint its golden file and review the gallery:

```sh
UPDATE_SNAPSHOTS=1 PYTHONPATH=src/core ./venv/bin/python3 -m unittest discover -s tests
./venv/bin/python3 tests/ui/gallery.py --surface run
```

For `--status` / `--ping` / config cases, use the matching driver
(`drive_service` / `drive_not_installed` / `drive_orphan`, `drive_ping`, `drive_config`)
with the input builders in `catalog/inputs.py`.

**Modify** — edit the scenario, regenerate (`UPDATE_SNAPSHOTS=1 …`), review the one-file diff.

**Remove** — delete the function *and* its `snapshots/<surface>__<name>.txt`. The
`test_no_orphan_snapshots` check fails if a golden file is left behind.

## How it stays deterministic

- The recording console has a fixed width (≥ the 75-char panel width), so footnote
  wrapping inside the panel is reproduced exactly.
- The console clock is pinned so the scraping `Spinner` renders a stable frame.
- Snapshots store plain text (`styles=False`) with per-line trailing whitespace stripped,
  so diffs are clean and color/ANSI never leaks into the golden files.
- Scenarios use fixed literals (prices, timestamps, exit codes), never `now()`.

A **RUN** scenario replays the exact sequence of `InteractiveExecutionStrategy` calls the
orchestrator makes; this encodes the orchestrator→strategy contract. If the orchestrator
changes which calls it emits, update the affected scenario and regenerate its snapshot.
