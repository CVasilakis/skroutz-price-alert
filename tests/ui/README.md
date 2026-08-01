# Terminal-UI test suite

This suite automatically verifies **everything Scrooge Alert draws in the terminal** — the
live scraping panel of a normal run, the `status`, `ping`, and Configuration Check
panels, and the colored transcripts printed by the management shell scripts
(`scripts/install.sh`, `scripts/update.sh`, and the other operational scripts). It exists so that
changes to the UI (a new
footnote, a reworded message, a border-color rule, a layout tweak) are caught the moment
they alter what a user would see, without anyone having to run the app and manually
reproduce failure states.

If you've never touched this folder, read the next two sections and you'll understand the
whole idea. If you're here to add a case, jump to
[Add / modify / remove a scenario](#add--modify--remove-a-scenario). Either way, please
read [Safety: never commit private data](#-safety-never-commit-private-data) before you
write a scenario.

---

## Why this exists (the problem it solves)

The UI is rendered with the [Rich](https://rich.readthedocs.io/) library into bordered
panels. Getting it right means caring about things that only show up *when rendered*:

- Does a long footnote **wrap** cleanly inside the configured panel width, or spill/overflow?
- Is the panel **border color** right (green = good, yellow = warning, red = error,
  blue = in-progress)?
- Do item names **truncate** correctly? Are the **icons** (✅ 🟡 ❗ 🛑 🎉 ⏳) the ones
  you expect? Are footnote reference numbers (`[1] [2] …`) aligned to the right notes?

Historically the only way to check these was to **run the app and induce each state by
hand** — turn off Wi-Fi to see a network error, corrupt a config to see a parse failure,
hit Ctrl+C at just the right moment to see an interrupt. That doesn't scale: there are
~200 distinct UI states, many of which are annoying or slow to reproduce on demand.

This suite reproduces every one of those states **deterministically, in milliseconds**,
and fails loudly if the rendered output ever changes unexpectedly.

---

## The core idea

Three things make this tractable:

1. **One catalog is the single source of truth.** Every UI state is described once, as a
   *scenario*, in `catalog/`. Nothing else duplicates that list.
2. **Scenarios drive the *real* production rendering code** with synthetic inputs — no
   network, no `systemctl`, no real config. So what you snapshot is exactly what ships,
   not a look-alike reimplementation.
3. **The same catalog feeds two consumers and both execution modes:**
   - a **snapshot gate** for the interactive rendering and background `output.log` files,
   - a **gallery** that shows the interactive rendering and background logs together.

```
                    ┌─────────────────────────┐
                    │   catalog/  (scenarios)  │   ← the single source of truth
                    │  run / status / ping /   │
                    │  config / sh-* (shell)   │
                    └────────────┬────────────┘
                                 │ each scenario.build() → BuildResult(UI + output logs)
                                 ▼
                    ┌─────────────────────────┐
                    │  harness/  (drivers +    │   ← runs the real production builders
                    │   deterministic render)  │     and scripts, captures text
                    └───────┬─────────────┬────┘
                            ▼             ▼
                 ┌────────────────┐  ┌────────────────┐
                 │ snapshot gate  │  │ HTML gallery   │
                 │ (UI + quiet    │  │ (interactive + │
                 │  log goldens)  │  │  output.log)   │
                 └────────────────┘  └────────────────┘
```

**Why snapshot testing?** Asserting "the panel contains the string X" by hand for ~200
states would be enormous and would still miss layout/wrapping. A snapshot captures the
*entire* rendered panel in one shot; the test just asks "is it byte-for-byte what we
approved last time?" You approve the output once (by generating the golden file and
reviewing it), and from then on any drift is flagged automatically.

**Why drive the real code instead of mocking the panels?** Fidelity. The drivers call the
actual functions the app uses at runtime (`core.tui.ping.build_ping_panel`,
`core.tui.status.build_service_panel`, the real `InteractiveRunReporter`, the real
`config_check` row helpers). A reimplementation could pass its own tests while the real UI
is broken. The root entry points collect inputs and delegate all rendering to these
presentation-only builders.

---

## Directory layout

```
tests/ui/
  catalog/                 # ── WHAT to render: the scenarios (edit these to add cases)
    _base.py               #    Scenario, Surface, BuildResult, the @scenario registrar
    inputs.py              #    shared input builders (resolved entries/settings,
                           #      ConfigView, systemd property dicts) — reused by scenarios
    run_scenarios.py       #    interactive scraping panel (a normal run)
    e2e_run_scenarios.py   #    the same panel, driven by the real application workflow
    config_scenarios.py    #    Configuration Check panel
    startup_scenarios.py   #    the full pre-scrape console transcript (surface startup)
    status_scenarios.py    #    status: service / not-installed / orphan panels
    ping_scenarios.py      #    ping: Notification Check Results
    shell_inputs.py        #    shared ShellWorld presets + the shell_case registrar
    sh_install_scenarios.py    # scripts/install.sh transcripts (surface sh-install)
    sh_run_scenarios.py        # scripts/run.sh               (surface sh-run)
    sh_ping_scenarios.py       # scripts/ping.sh              (surface sh-ping)
    sh_status_scenarios.py     # scripts/status.sh            (surface sh-status)
    sh_schedule_scenarios.py   # scripts/schedule.sh          (surface sh-schedule)
    sh_enable_scenarios.py     # scripts/enable.sh            (surface sh-enable)
    sh_disable_scenarios.py    # scripts/disable.sh           (surface sh-disable)
    sh_stop_scenarios.py       # scripts/stop.sh              (surface sh-stop)
    sh_update_scenarios.py     # scripts/update.sh transcripts  (surface sh-update)
    sh_uninstall_scenarios.py  # scripts/uninstall.sh         (surface sh-uninstall)
    __init__.py            #    imports every module above and exposes ALL_SCENARIOS
  harness/                 # ── HOW to render: turn a scenario into captured text
    drivers.py             #    drive_run / drive_service / drive_ping / drive_config, ...
    shell.py               #    drive_shell: sandboxed shell-script execution + shims
    rendering.py           #    the deterministic recording console + capture helpers
  snapshots/               # ── the approved output
    terminal/              #    terminal golden files <surface>__<name>.txt
    background/            #    matching output.log goldens for run surfaces
  test_ui_catalog.py       #    catalog metadata, visibility, and ordering guards
  test_ui_snapshots.py     #    the gate: compare each scenario to its golden file
  test_ui_colors.py        #    border-color assertions
  test_html_report.py      #    interactive/background artifact-switching behavior
  gallery.py               #    render everything in color for human review
  README.md                #    you are here
```

A useful way to hold it in your head: **`catalog/` says *what*, `harness/` says *how*,
`snapshots/` is the *approved result*, and the `test_*.py` files plus `gallery.py` are
the *consumers*.**

---

## Running it

Everything runs through the project's venv. Tests are written with the stdlib `unittest`
`TestCase` API but run under **pytest**, which is configured in the repo-root
`pyproject.toml` (`pythonpath = ["src", "tests"]`, `testpaths = ["tests"]`). Install
the complete development toolchain once with `./scripts/dev/setup.sh`.

### Run the whole suite

```sh
./venv/bin/python3 -m pytest
```

The `pythonpath` setting lets the scenarios import the production modules
(`core.tui.run_reporter`, `core.tui.status`,
`core.tui.ping`, …) and the `ui.*` test packages the same way the app does — no `PYTHONPATH=` prefix
needed. This runs the existing project tests **and** the UI catalog, snapshot, and color tests together
(`pytest tests/ui` runs just this suite).

### Read a failure

When output changes, you get a per-scenario failure naming the exact case, e.g.:

```
FAIL: test_matches_snapshot (... scenario='status__exec_target_config_error')
AssertionError: ... != ...  : UI output changed for 'status__exec_target_config_error'. ...
```

Two outcomes are possible:

- **You didn't mean to change it** → a real regression. Fix the code.
- **You did mean to change it** → regenerate the golden file (below) and review the diff.

The clearest way to *see* what changed is to regenerate and let git show you:

```sh
UPDATE_SNAPSHOTS=1 ./venv/bin/python3 -m pytest
git diff -- tests/ui/snapshots/          # review every changed panel, line by line
```

If the diff is what you intended, commit it. If not, `git checkout -- tests/ui/snapshots/`
to throw the regeneration away and go fix the code instead. **Regenerating is "I approve
this new output" — always review the diff before committing it.**

### Regenerate the golden files

```sh
UPDATE_SNAPSHOTS=1 ./venv/bin/python3 -m pytest
```

Setting `UPDATE_SNAPSHOTS=1` makes the snapshot test *write* each golden file instead of
comparing. Use it when you add a scenario, or after an intended UI change.

### Eyeball the panels (the gallery)

The terminal gallery renders interactive scenarios in **full color**. The HTML report
also includes the production-formatted background logs captured from the same scenario
inputs.

```sh
./venv/bin/python3 tests/ui/gallery.py                 # every scenario, grouped by surface
./venv/bin/python3 tests/ui/gallery.py --surface run   # only the selected surface
./venv/bin/python3 tests/ui/gallery.py --tag interrupt # only scenarios carrying a tag
./venv/bin/python3 tests/ui/gallery.py --html /tmp/ui.html   # write a shareable HTML page
./venv/bin/python3 tests/ui/gallery.py --list          # list scenario keys + descriptions (renders nothing)
```

| Option        | What it does                                                                 |
|---------------|------------------------------------------------------------------------------|
| *(none)*      | Prints every scenario to the terminal with real ANSI color, grouped by surface. |
| `--surface S` | Limits to one surface: `run`, `e2e-run`, `status`, `ping`, `config`, `startup`, or a shell surface (`sh-install`, `sh-update`, `sh-schedule`, `sh-enable`, `sh-disable`, `sh-stop`, `sh-run`, `sh-uninstall`). |
| `--tag T`     | Limits to scenarios tagged `T` (e.g. `retry`, `interrupt`, `layout`, `settings`). `T` must be one of the curated tags in `TAG_VOCABULARY` (`catalog/_base.py`); the flag rejects anything else. |
| `--html PATH` | Writes one self-contained report with interactive renderings and switchable `output.log` artifacts for sharing or archiving. |
| `--list`      | Prints each (optionally filtered) scenario's key and one-line description, then a count, and exits without rendering. Great for discovering what exists. |

Test-only scenarios (`in_gallery=False`, currently the `startup` layout guards) keep
their duplicated interactive transcripts hidden from unfiltered output. Their unique
target and reminder logs do appear in the default HTML report. An explicit filter such
as `--surface startup` or `--tag layout` reveals the interactive transcript too.

Section headers (terminal rules and the HTML report's sections) show each surface's
human-readable label from `SURFACE_INFO` (`catalog/_base.py`) — e.g. `sh-install`
renders as **install.sh** and `startup` as **Full startup transcript** — while the
`--surface` values and snapshot filenames keep the stable machine names.

`--surface` and `--tag` combine, and both work with `--list` and `--html`. The gallery
needs no environment setup — it adds `src/` (the `core` package root) and `tests/` to the path itself, so
`./venv/bin/python3 tests/ui/gallery.py …` just works.

---

## How a scenario works

A **scenario** is a small record (`catalog/_base.py`):

```python
@dataclass(frozen=True)
class Scenario:
    name: str            # unique within its surface, snake_case
    surface: Surface     # RUN | STATUS | PING | CONFIG | SH_INSTALL | ... | SH_UNINSTALL
    description: str      # one line, shown as the gallery header
    build: Callable[[], BuildResult]   # produces the renderable + its border color
    tags: tuple[str, ...] = ()          # optional filter labels, from TAG_VOCABULARY
```

Tags come from one curated vocabulary — `TAG_VOCABULARY` in `catalog/_base.py`, a
`tag -> one-line meaning` mapping (currently: `ok`, `error`, `skipped`, `help`,
`retry`, `interrupt`, `in_progress`, `price_drop`, `listing`, `settings`, `target_config`,
`reminder`, `timer`, `last_run`, `orphan`, `catalog`, `system`, `combined`,
`layout`, `synthetic`). `test_ui_catalog.py` rejects a tag outside the vocabulary and a
vocabulary entry no scenario uses, so the filter chips in the HTML report stay
small and meaningful. To introduce a tag, add it there with its meaning.

You never construct one by hand — the `@scenario(...)` decorator registers the function it
wraps. `build()` returns a `BuildResult(renderable, border_color, exit_code, output_logs)`:

- `renderable` is the actual Rich object to draw (a `Panel`, a `StatusPanelBuilder`, or —
  for the shell surfaces — a `Text` transcript parsed from the script's ANSI output),
- `border_color` is the color the panel border resolves to. It's recorded as the first
  line of the golden file (`# border: red`) so a **color change shows up as a one-line
  diff** even though the snapshot body is plain (uncolored) text. Shell scenarios derive
  it from the exit code: 0 → green, anything else → red.
- `exit_code` is the script's exit status for shell scenarios (`None` for the panel
  surfaces), recorded as a second header line (`# exit: 1`).
- `output_logs` is an immutable collection of relative path + content artifacts. It is
  populated automatically for `RUN`, `E2E_RUN`, and `STARTUP` scenarios and empty for
  surfaces without a background-scraper equivalent.

The `snapshot_key` is `"<surface>__<name>"` — that's the golden filename stem and the id
you see in test output and `--list`.

### The surfaces, and what each drives

Each surface has a **driver** in `harness/drivers.py` that feeds synthetic inputs to the
real production builder:

| Surface  | Gallery label | Driver(s)                                                   | Drives (production code)                              |
|----------|---------------|-------------------------------------------------------------|------------------------------------------------------|
| `RUN`    | Scraping panel (interactive) | `drive_run(script)`                                         | the real `run_reporter.InteractiveRunReporter` panel    |
| `E2E_RUN`| Scraping panel (end-to-end) | `drive_orchestrated_run(items, results_by_url)`             | the real application workflow driving that same panel |
| `STATUS` | Health check (status) | `drive_service(…, config)`, `drive_not_installed`, `drive_orphan` | `status.build_service_panel` / …               |
| `PING`   | Notification check (ping) | `drive_ping(url_entries, test_results, config_error_msg)`   | `ping.build_ping_panel`                              |
| `CONFIG` | Configuration Check panel | `drive_config(version_state, …)`                            | `config_check.build_config_panel`                    |
| `STARTUP`| Full startup transcript (interactive artifact test-only) | `drive_startup(run_script, …)`                              | the whole pre-scrape transcript plus target/reminder background logs (guards against text leaking *between* panels; see `test_ui_snapshots.TestNoTextOutsidePanels`) |
| `SH_*`   | the script filename (e.g. install.sh) | `drive_shell(script, *args, world=…, stdin=…)`              | the real operational scripts under `scripts/`        |

The per-scraper **target-configuration health** (the `Config` row) is no longer a `CONFIG`-surface
concern: it leads each `STATUS` Service Status panel (`drive_service`'s `config`) and each
`RUN` Scraping panel (the `config` passed to `_start`/`start_target`), built by the shared
`config_check.config_view` / `add_config_row`.

**RUN is special.** The scraping panel is *live* — it evolves as items are checked. So a
RUN scenario is a **script**: a sequence of the exact method calls the application workflow
makes on the reporter, in order, ending at the moment you want to capture. The driver runs
the same script against the real `InteractiveRunReporter` and `SilentRunReporter`. It
captures the resulting panel and the real file logger's production-formatted
`logs/<target>/output.log` without duplicating the scenario:

```python
@scenario(Surface.RUN, "retry_then_drop", "Attempt 1 failed, attempt 2 dropped below target", tags=("retry", "price_drop"))
def _():
    def script(s):
        _start(s)                                   # opens the target with a Config row + settings section
        s.start_scraping("Widget", 1, 3)
        s.complete_scraping()
        s.log_attempt("Widget", 1, 3, "ScraperParseError: ...")   # attempt 1 failed
        s.start_scraping("Widget", 2, 3)
        s.complete_scraping()
        s.log_price_result("Widget", 9.99, CURRENCY, 12.0, PriceOutcome.DROP,
                           notes=["Succeeded on attempt 2/3", NOTIFIED_OK],
                           attempt_notes=["Attempt 1: ScraperParseError"])
        s.complete_target()      # end a FINISHED panel here (settles the final border color)
    return drive_run(script)
```

The **capture point is wherever the script ends**:

- End with `s.complete_target()` to capture a *finished* target (this settles the border to
  its final green/red/yellow).
- **Omit** `complete_target()` to capture a *mid-flight* state — e.g. stop right after
  `s.start_scraping(...)` to snapshot the spinner, or after `s.start_sleep(...)` +
  `s.update_sleep(...)` to snapshot the progress bar (these render blue, "in progress").

Because a RUN script mirrors the application workflow's real call sequence, it doubles as
executable documentation of the application→reporter contract. If the workflow ever
changes *which* calls it emits for a situation, update the matching scenario and
regenerate both goldens with the usual `UPDATE_SNAPSHOTS=1` command. The note strings a script feeds the reporter are imported from the
production message catalog (`core.messages`), so their wording can never drift from what
the application emits.

**E2E_RUN closes the loop from the other end.** Where a RUN script hand-feeds the
reporter, an `E2E_RUN` scenario (`e2e_run_scenarios.py`) gives `drive_orchestrated_run`
only the config rows and each item's scrape outcomes (price/listing results or exceptions);
the real application workflow then runs against a scripted client and a JSON state
repository in a temp dir, and whatever notes it actually emits land on the captured panel.
A change to the application's UI payloads (wording, ordering, which notes appear at
all) flips these goldens even if the hand-scripted catalog were forgotten. Keep this
surface to the main note-producing flows; states the workflow can't finish
deterministically (spinners, sleeps, interrupts, stale timestamps) belong in RUN.
The driver executes those inputs in both interactive and quiet modes with fresh temporary
configuration/state, so its background golden comes from the real orchestrator and file
logger. `STARTUP` similarly captures every generated `output.log`, including the separate
`logs/reminder/output.log` produced by `ReminderService`.

The other three panel surfaces are *static*, so their scenarios just hand the driver the
inputs and return its result directly — no script:

```python
@scenario(Surface.STATUS, "invalid_retention", "log_retention_days out of range", tags=("settings",))
def _():
    resolved = resolved_settings(retention=(7, STATUS_INVALID, 99))   # from inputs.py
    return drive_service("skroutz", timer_props(True), service_props(), resolved,
                         "skroutz.json", "hourly", "hourly")
```

### The shell surfaces (`sh-*`)

A shell scenario snapshots the transcript one management script prints for one world
state. `drive_shell` (`harness/shell.py`) copies the **real scripts** into a throwaway
install tree, shims every external command they touch (`systemctl`, `loginctl`, `git`,
`python3`, and the venv python that answers the catalog queries in
`scripts/lib/common.sh`), runs the script with `/bin/sh`, and captures stdout+stderr
interleaved — exactly what a terminal user sees. Nothing touches your real system: no
systemd, no git, no network, no real venv.

The world state is a `ShellWorld` (which plugins are registered/installed, what systemd
reports, which commands fail, …); `catalog/shell_inputs.py` provides named presets
(`WORLD_HEALTHY`, `WORLD_ORPHAN`, `WORLD_NO_VENV`, …) and the `shell_case` registrar
that keeps each scenario to a few declarative lines:

```python
_case = shell_case(Surface.SH_ENABLE, "scripts/enable.sh")

_case("enable_fails", "systemctl enable --now fails.",
      world=replace(WORLD_INSTALLED, systemctl_fail=("enable",)), tags=("error",))
```

The cast is fixed across all shell scenarios: `skroutz` (healthy, installed), `amazon`
(registered but not installed), `ghost` (an orphan — units on disk, plugin removed).
Scenarios that need standard input supply it through `stdin=`. The sandbox path is
normalized to `<BASE_DIR>` and sh's own diagnostics to `<line>`, so the goldens are
machine-independent.

### Shared input builders (`catalog/inputs.py`)

So scenarios stay short and use the *real* production types (with their real display
formatting and warning text), `inputs.py` offers small factories:

- `interval_view / retention_view / notify_view(value, status, raw)` — a single
  resolved-setting entry; `views_all_ok() / views_all_default() /
  views_one_invalid_each()` provide ready-made settings sections.
- `resolved_settings(interval=…, retention=…, notify=…)` — a full
  `ResolvedSettings` for status.
- `timer_props(...)` / `service_props(...)` — the systemd property dicts status reads.
- `config_ok(...) / config_faulty(...) / config_failed(...)` — target
  configuration row outcomes.
- `stub_logger()`, `CURRENCY` — misc helpers.

`run_scenarios.py` also defines a `_start(s, …)` helper and note constants (`NOTIFIED_OK`,
`ERRORS_LOG`, `STALE`, …) that mirror the exact strings the application emits.

---

## Add / modify / remove a scenario

### Add

1. Pick the right `catalog/*_scenarios.py` file for the surface.
2. Write one `@scenario(...)`-decorated function that returns a driver call (use the
   examples above and the existing scenarios as templates). Give it a **unique**
   snake_case `name` and a clear one-line `description`; add `tags` if useful for gallery
   filtering (they must come from `TAG_VOCABULARY` in `catalog/_base.py`).
3. Mint its golden file and review it:
   ```sh
   UPDATE_SNAPSHOTS=1 ./venv/bin/python3 -m pytest
   ./venv/bin/python3 tests/ui/gallery.py --surface <surface>   # look at it in color
   git diff -- tests/ui/snapshots/                              # review the new golden file
   ```

No harness or test file needs editing — the tests and gallery iterate `ALL_SCENARIOS`
automatically.

### Modify

Edit the scenario, regenerate (`UPDATE_SNAPSHOTS=1 …`), and review the one-file diff before
committing. If the diff surprises you, it's telling you the change had a side effect you
didn't expect — investigate before approving.

### Remove

Delete the function, its `snapshots/terminal/<surface>__<name>.txt`, and—when applicable—its
`snapshots/background/<surface>__<name>.txt`. The
`test_no_orphan_snapshots` check fails on a golden file with no owning scenario, so you
can't leave one behind by accident.

---

## The golden files

Each `snapshots/terminal/<surface>__<name>.txt` looks like:

```
# border: green

╭─────────────────────────── Skroutz Scraping ────────────────────────────╮
│   ✅    Execution Interval    1h (default)                              │
│   🎉    Widget                9.99 € (Target: 12.0 €) [1]               │
│                                                                         │
│   [1] Notification delivered to all valid apprise URL(s).               │
╰─────────────────────────────────────────────────────────────────────────╯
```

A shell golden adds the script's exit status to the header:

```
# border: red
# exit: 1

[skroutz] Enabling and starting background schedule (timer)...
[skroutz] Error: Failed to enable the timer!
Try running ./scrooge-alert install to fix the issue.
```

- The **`# border:` header** records the resolved border color, so color regressions are a
  one-line diff. For shell scenarios it is derived from the exit code (0 → green, else
  red), and the **`# exit:` header** pins the exit status itself.
- The **body is plain text** (no ANSI escape codes). That's a deliberate choice: colored
  golden files would be an unreadable mess of escape sequences, and every layout change
  would move the codes around and bury the real diff. Layout/wrapping/text live in the
  body; color lives in the header; the gallery covers "the actual colors look right."

**These files are committed on purpose.** They are the approved reference the gate compares
against — without them, a fresh clone or CI run has nothing to check against and every test
errors with "missing snapshot." (They're `.txt`, which this repo's `.gitignore` blanket-
ignores, so `.gitignore` explicitly un-ignores both snapshot directories. Don't remove
those rules.)

---

## ⚠️ Safety: never commit private data

**Golden files and scenario source are committed to git.** Whatever you put in a scenario
becomes part of the repository history. So:

- **Never use a real notification URL, API token, webhook, or secret** as a fixture. Use
  obvious fakes: `tgram://1...n/...`, `slack://***@workspace/channel/...`. Even a *fake*
  string that merely *looks* like a token can trip GitHub's push protection — a Slack
  `xoxb-…` shape, a real-looking Telegram bot token (`digits:35-char-string`), a
  `discord.com/api/webhooks/...` URL, an AWS key, etc. Keep placeholders clearly synthetic.
- **Don't paste your real tracked items.** Use invented names (`Sony WH-1000XM5`,
  `Widget`), not the items from your own `config/skroutz.json`.
- **Don't bake in personal paths, usernames, or emails** (`/home/you/...`,
  `you@example.com`). None are needed — scenarios take literal inputs you control.

Quick self-check before committing new scenarios/snapshots:

```sh
grep -rIn -e 'xoxb-' -e 'https://' -e '@gmail' -e '/home/' tests/ui/snapshots tests/ui/catalog
```

This is safe *by construction* today: no driver ever reads your real config,
environment, or `systemctl` — `drive_config` patches those seams, the others take
literal fixtures, and `drive_shell` builds a from-scratch environment inside a temp
sandbox (fake HOME, fake PATH, canned shim output — the transcripts are fully
synthetic). Keep it that way: don't introduce a scenario that reads live data.

---

## How determinism is guaranteed

A snapshot is only useful if the same input always renders the same bytes. The harness
enforces that:

- **Fixed width.** The recording console is wider than the configured/default panel, so the
  panel and its footnote wrapping are reproduced identically regardless of your real
  terminal size.
- **Pinned clock.** The scraping `Spinner` picks its frame from the clock; the capture
  console's clock is pinned so it always renders the same frame.
- **No live animation.** For RUN, Rich's live-refresh loop is stubbed out, so the reporter
  just accumulates state and we capture the final `Panel` — no timing races, no partial
  frames.
- **Plain text, trimmed.** Output is captured with styles off and per-line trailing spaces
  stripped, so console padding and color never leak into the golden file.
- **Literal inputs only.** Scenarios use fixed prices, timestamps, and exit codes — never
  `now()`, `random`, or anything environment-dependent. (If you add a scenario that pulls a
  live value, its snapshot will flap. Don't.)
- **Sandboxed shell runs.** `drive_shell` never inherits your environment: PATH points at
  the sandbox's shims (plus `/usr/bin:/bin` for coreutils), HOME and the systemd user dir
  live inside the sandbox, `LC_ALL=C`, and every shim's output is canned. The sandbox path
  is rewritten to `<BASE_DIR>` and sh's own diagnostic line numbers to `<line>`, so the
  goldens don't depend on where or on which machine they were generated. (They do assume
  `/bin/sh` is dash-compatible — true on Debian/Ubuntu/WSL and the CI runners.)

---

## The test files (what actually asserts)

- **`test_ui_catalog.py`** — guards tag vocabulary, surface metadata, hidden-scenario
  visibility, and section ordering.
- **`test_ui_snapshots.py`** — for every scenario: compare its interactive and applicable
  background artifacts to their golden files (or write them under
  `UPDATE_SNAPSHOTS=1`). Also checks scenario keys and rejects orphan goldens.
- **`test_ui_colors.py`** — asserts every scenario resolves to a *valid* border color, and
  pins a curated, representative set to a *specific* expected color (one per color-decision
  branch, per surface). This guards the color logic directly, independent of the snapshot
  header.
- **`test_html_report.py`** — asserts artifact tabs, escaping, and the visibility rules
  for test-only interactive startup transcripts.

---

## FAQ / rationale

**Why not just assert on substrings?** You'd need dozens of asserts per state and still
miss wrapping, alignment, truncation, and border color. A snapshot captures all of it at
once.

**Why is a "green" border sometimes shown on a panel that also has an error row?** Because
that's the real rule: a 🎉 price-drop celebration outranks an ❗ error in the border-color
priority. The snapshot faithfully captures production behavior — including behavior you
might want to reconsider. That's a feature: the test shows you what users actually see.

**A snapshot changed and I don't understand why.** Render it in the gallery
(`gallery.py --surface … --tag …`) to see it in color, and `git diff tests/ui/snapshots/`
to see the exact textual change. The change is real production output — trace back from the
diff to the code that produced it.

**Do I need to touch `harness/` or the test files to add a case?** No. They're generic over
`ALL_SCENARIOS`. Adding, editing, or removing a case is a one-file edit in `catalog/`
(plus its golden file).
