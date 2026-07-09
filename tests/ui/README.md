# Terminal-UI test suite

This suite automatically verifies **everything Scrooge Alert draws in the terminal** — the
live scraping panel of a normal run, the `--status`, `--ping`, and Configuration Check
panels, and the colored transcripts printed by the management shell scripts
(`install.sh`, `update.sh`, `scripts/*.sh`). It exists so that changes to the UI (a new
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

- Does a long footnote **wrap** cleanly inside the 75-character panel, or spill/overflow?
- Is the panel **border color** right (green = good, yellow = warning, red = error,
  blue = in-progress)?
- Do product names **truncate** correctly? Are the **icons** (✅ 🟡 ❗ 🛑 🎉 ⏳) the ones
  you expect? Are footnote reference numbers (`[1] [2] …`) aligned to the right notes?

Historically the only way to check these was to **run the app and induce each state by
hand** — turn off Wi-Fi to see a network error, corrupt a config to see a parse failure,
hit Ctrl+C at just the right moment to see an interrupt. That doesn't scale: there are
~85 distinct UI states, many of which are annoying or slow to reproduce on demand.

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
3. **The same catalog feeds two consumers:**
   - a **snapshot gate** (machine check — did the output change?),
   - a **gallery** (human check — does it *look* right?).

```
                    ┌─────────────────────────┐
                    │   catalog/  (scenarios)  │   ← the single source of truth
                    │  run / status / ping /   │
                    │  config / sh-* (shell)   │
                    └────────────┬────────────┘
                                 │ each scenario.build() → BuildResult(renderable, color)
                                 ▼
                    ┌─────────────────────────┐
                    │  harness/  (drivers +    │   ← runs the real production builders
                    │   deterministic render)  │     and scripts, captures text
                    └───────┬─────────────┬────┘
                            ▼             ▼
                 ┌────────────────┐  ┌────────────────┐
                 │ snapshot gate  │  │    gallery     │
                 │ (plain-text    │  │ (full color,   │
                 │  golden files) │  │  for eyeballs) │
                 └────────────────┘  └────────────────┘
```

**Why snapshot testing?** Asserting "the panel contains the string X" by hand for 85
states would be enormous and would still miss layout/wrapping. A snapshot captures the
*entire* rendered panel in one shot; the test just asks "is it byte-for-byte what we
approved last time?" You approve the output once (by generating the golden file and
reviewing it), and from then on any drift is flagged automatically.

**Why drive the real code instead of mocking the panels?** Fidelity. The drivers call the
actual functions the app uses at runtime (`ping.build_ping_panel`,
`status.build_service_panel`, the real `InteractiveExecutionStrategy`, the real
`config_check` row helpers). A reimplementation could pass its own tests while the real UI
is broken. (These builder functions were deliberately extracted from the `main()` bodies
of `ping.py`/`status.py` so they could be called in isolation — same pattern
`config_check.py` already used.)

---

## Directory layout

```
tests/ui/
  catalog/                 # ── WHAT to render: the scenarios (edit these to add cases)
    _base.py               #    Scenario, Surface, BuildResult, the @scenario registrar
    inputs.py              #    shared input builders (SettingViews, ResolvedSettings,
                           #      ConfigView, systemd property dicts) — reused by scenarios
    run_scenarios.py       #    interactive scraping panel (a normal run)
    status_scenarios.py    #    --status: service / not-installed / orphan panels
    ping_scenarios.py      #    --ping: Notification Check Results
    config_scenarios.py    #    Configuration Check panel
    shell_inputs.py        #    shared ShellWorld presets + the shell_case registrar
    sh_install_scenarios.py    # install.sh transcripts       (surface sh-install)
    sh_update_scenarios.py     # update.sh transcripts        (surface sh-update)
    sh_schedule_scenarios.py   # scripts/schedule.sh          (surface sh-schedule)
    sh_enable_scenarios.py     # scripts/enable.sh            (surface sh-enable)
    sh_disable_scenarios.py    # scripts/disable.sh           (surface sh-disable)
    sh_stop_scenarios.py       # scripts/stop.sh              (surface sh-stop)
    sh_run_scenarios.py        # scripts/run.sh               (surface sh-run)
    sh_uninstall_scenarios.py  # scripts/uninstall.sh         (surface sh-uninstall)
    __init__.py            #    imports every module above and exposes ALL_SCENARIOS
  harness/                 # ── HOW to render: turn a scenario into captured text
    drivers.py             #    drive_run / drive_service / drive_ping / drive_config, ...
    shell.py               #    drive_shell: sandboxed shell-script execution + shims
    rendering.py           #    the deterministic recording console + capture helpers
  snapshots/               # ── the approved output: golden files <surface>__<name>.txt
  test_ui_snapshots.py     #    the gate: compare each scenario to its golden file
  test_ui_colors.py        #    border-color assertions
  gallery.py               #    render everything in color for human review
  README.md                #    you are here
```

A useful way to hold it in your head: **`catalog/` says *what*, `harness/` says *how*,
`snapshots/` is the *approved result*, and the two `test_*.py` files plus `gallery.py` are
the *consumers*.**

---

## Running it

Everything runs through the project's venv. Tests are written with the stdlib `unittest`
`TestCase` API but run under **pytest**, which is configured in the repo-root
`pyproject.toml` (`pythonpath = ["src", "tests"]`, `testpaths = ["tests"]`). Install
the test toolchain once with `./venv/bin/python3 -m pip install -r requirements-dev.txt`.

### Run the whole suite

```sh
./venv/bin/python3 -m pytest
```

The `pythonpath` setting lets the scenarios import the production modules (`core.ui.tui`, `core.status`,
`core.ping`, …) and the `ui.*` test packages the same way the app does — no `PYTHONPATH=` prefix
needed. This runs the existing project tests **and** the UI snapshot + color tests together
(`pytest tests/ui` runs just this suite).

### Read a failure

When output changes, you get a per-scenario failure naming the exact case, e.g.:

```
FAIL: test_matches_snapshot (... scenario='status__exec_products_error')
AssertionError: ... != ...  : UI output changed for 'status__exec_products_error'. ...
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

The gallery renders scenarios in **full color** to your terminal — the "does it look
right?" check that snapshots (plain text) can't give you.

```sh
./venv/bin/python3 tests/ui/gallery.py                 # every scenario, grouped by surface
./venv/bin/python3 tests/ui/gallery.py --surface run   # only one surface: run|status|ping|config
./venv/bin/python3 tests/ui/gallery.py --tag interrupt # only scenarios carrying a tag
./venv/bin/python3 tests/ui/gallery.py --html /tmp/ui.html   # write a shareable HTML page
./venv/bin/python3 tests/ui/gallery.py --list          # list scenario keys + descriptions (renders nothing)
```

| Option        | What it does                                                                 |
|---------------|------------------------------------------------------------------------------|
| *(none)*      | Prints every scenario to the terminal with real ANSI color, grouped by surface. |
| `--surface S` | Limits to one surface: `run`, `status`, `ping`, `config`, or a shell surface (`sh-install`, `sh-update`, `sh-schedule`, `sh-enable`, `sh-disable`, `sh-stop`, `sh-run`, `sh-uninstall`). |
| `--tag T`     | Limits to scenarios tagged `T` (e.g. `retry`, `interrupt`, `layout`, `settings`). |
| `--html PATH` | Renders the same output into one self-contained HTML file (colors preserved) for sharing or archiving, instead of printing. |
| `--list`      | Prints each (optionally filtered) scenario's key and one-line description, then a count, and exits without rendering. Great for discovering what exists. |

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
    tags: tuple[str, ...] = ()          # optional filter labels
```

You never construct one by hand — the `@scenario(...)` decorator registers the function it
wraps. `build()` returns a `BuildResult(renderable, border_color, exit_code)`:

- `renderable` is the actual Rich object to draw (a `Panel`, a `StatusPanelBuilder`, or —
  for the shell surfaces — a `Text` transcript parsed from the script's ANSI output),
- `border_color` is the color the panel border resolves to. It's recorded as the first
  line of the golden file (`# border: red`) so a **color change shows up as a one-line
  diff** even though the snapshot body is plain (uncolored) text. Shell scenarios derive
  it from the exit code: 0 → green, anything else → red.
- `exit_code` is the script's exit status for shell scenarios (`None` for the panel
  surfaces), recorded as a second header line (`# exit: 1`).

The `snapshot_key` is `"<surface>__<name>"` — that's the golden filename stem and the id
you see in test output and `--list`.

### The surfaces, and what each drives

Each surface has a **driver** in `harness/drivers.py` that feeds synthetic inputs to the
real production builder:

| Surface  | Driver(s)                                                   | Drives (production code)                              |
|----------|-------------------------------------------------------------|------------------------------------------------------|
| `RUN`    | `drive_run(script)`                                         | the real `tui.InteractiveExecutionStrategy` panel    |
| `E2E_RUN`| `drive_orchestrated_run(products, results_by_url)`          | the real `ScrapingOrchestrator` driving that same panel |
| `STATUS` | `drive_service(…, config)`, `drive_not_installed`, `drive_orphan` | `status.build_service_panel` / …               |
| `PING`   | `drive_ping(url_entries, test_results, env_error_msg)`      | `ping.build_ping_panel`                              |
| `CONFIG` | `drive_config(version_state, …)`                            | `config_check._append_*` row helpers (version + .env) |
| `SH_*`   | `drive_shell(script, *args, world=…, stdin=…)`              | the real `install.sh` / `update.sh` / `scripts/*.sh`  |

The per-scraper **products-config health** (the `Config` row) is no longer a `CONFIG`-surface
concern: it leads each `STATUS` Service Status panel (`drive_service`'s `config`) and each
`RUN` Scraping panel (the `config` passed to `_start`/`start_target`), built by the shared
`config_check.config_view` / `add_config_row`.

**RUN is special.** The scraping panel is *live* — it evolves as products are checked. So a
RUN scenario is a **script**: a sequence of the exact method calls the orchestrator makes
on the strategy, in order, ending at the moment you want to capture. The driver runs the
script against a real strategy (with the live-refresh loop stubbed out so nothing animates)
and captures the resulting panel:

```python
@scenario(Surface.RUN, "retry_then_drop", "Attempt 1 failed, attempt 2 dropped below target", tags=("retry", "drop"))
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

Because a RUN script mirrors the orchestrator's real call sequence, it doubles as
executable documentation of the orchestrator→strategy contract. If the orchestrator ever
changes *which* calls it emits for a situation, update the matching scenario and
regenerate. The note strings a script feeds the strategy are imported from the
production catalog (`core.messages`), so their wording can never drift from what the
orchestrator emits.

**E2E_RUN closes the loop from the other end.** Where a RUN script hand-feeds the
strategy, an `E2E_RUN` scenario (`e2e_run_scenarios.py`) gives `drive_orchestrated_run`
only the config rows and each product's scrape outcomes (`ScrapeResult`s / exceptions);
the *real* `ScrapingOrchestrator` then runs against a scripted client and a real JSON
storage on a temp dir, and whatever notes it actually emits land on the captured panel.
A change to the orchestrator's UI payloads (wording, ordering, which notes appear at
all) flips these goldens even if the hand-scripted catalog were forgotten. Keep this
surface to the main note-producing flows; states the orchestrator can't finish
deterministically (spinners, sleeps, interrupts, stale timestamps) belong in RUN.

The other three panel surfaces are *static*, so their scenarios just hand the driver the
inputs and return its result directly — no script:

```python
@scenario(Surface.STATUS, "invalid_retention", "log_retention_days out of range", tags=("service", "settings"))
def _():
    resolved = resolved_settings(retention=(7, STATUS_INVALID, 99))   # from inputs.py
    return drive_service("skroutz", timer_props(True), service_props(), resolved,
                         "skroutz.json", "hourly", "hourly")
```

### The shell surfaces (`sh-*`)

A shell scenario snapshots the transcript one management script prints for one world
state. `drive_shell` (`harness/shell.py`) copies the **real scripts** into a throwaway
install tree, shims every external command they touch (`systemctl`, `loginctl`, `git`,
`python3`, and the venv python that answers the registry queries in
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
Interactive prompts (update.sh's dirty-tree confirmation) are fed via `stdin=`; the
sandbox path is normalized to `<BASE_DIR>` and sh's own diagnostics to `<line>`, so the
goldens are machine-independent.

### Shared input builders (`catalog/inputs.py`)

So scenarios stay short and use the *real* production types (with their real display
formatting and warning text), `inputs.py` offers small factories:

- `interval_view / retention_view / notify_view(value, status, raw)` — a single
  `SettingView` row; `views_all_ok() / views_all_default() / views_one_invalid_each()` —
  ready-made sets for the settings section.
- `resolved_settings(interval=…, retention=…, notify=…, block_warning=…)` — a full
  `ResolvedSettings` for `--status`.
- `timer_props(...)` / `service_props(...)` — the systemd property dicts `--status` reads.
- `target_load(...)` — a Configuration Check row outcome.
- `stub_logger()`, `CURRENCY` — misc helpers.

`run_scenarios.py` also defines a `_start(s, …)` helper and note constants (`NOTIFIED_OK`,
`ERRORS_LOG`, `STALE`, …) that mirror the exact strings the orchestrator emits.

---

## Add / modify / remove a scenario

### Add

1. Pick the right `catalog/*_scenarios.py` file for the surface.
2. Write one `@scenario(...)`-decorated function that returns a driver call (use the
   examples above and the existing scenarios as templates). Give it a **unique**
   snake_case `name` and a clear one-line `description`; add `tags` if useful for gallery
   filtering.
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

Delete the function **and** its `snapshots/<surface>__<name>.txt`. The
`test_no_orphan_snapshots` check fails on a golden file with no owning scenario, so you
can't leave one behind by accident.

---

## The golden files

Each `snapshots/<surface>__<name>.txt` looks like:

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
Try running ./install.sh to fix the issue.
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
ignores, so there's an explicit `!tests/ui/snapshots/*.txt` un-ignore rule keeping them
tracked. Don't remove it.)

---

## ⚠️ Safety: never commit private data

**Golden files and scenario source are committed to git.** Whatever you put in a scenario
becomes part of the repository history. So:

- **Never use a real notification URL, API token, webhook, or secret** as a fixture. Use
  obvious fakes: `tgram://1...n/...`, `slack://***@workspace/channel/...`. Even a *fake*
  string that merely *looks* like a token can trip GitHub's push protection — a Slack
  `xoxb-…` shape, a real-looking Telegram bot token (`digits:35-char-string`), a
  `discord.com/api/webhooks/...` URL, an AWS key, etc. Keep placeholders clearly synthetic.
- **Don't paste your real tracked products.** Use invented names (`Sony WH-1000XM5`,
  `Widget`), not the items from your own `config/skroutz.json`.
- **Don't bake in personal paths, usernames, or emails** (`/home/you/...`,
  `you@example.com`). None are needed — scenarios take literal inputs you control.

Quick self-check before committing new scenarios/snapshots:

```sh
grep -rIn -e 'xoxb-' -e 'https://' -e '@gmail' -e '/home/' tests/ui/snapshots tests/ui/catalog
```

This is safe *by construction* today: no driver ever reads your real `.env`, config,
environment, or `systemctl` — `drive_config` patches those seams, the others take
literal fixtures, and `drive_shell` builds a from-scratch environment inside a temp
sandbox (fake HOME, fake PATH, canned shim output — the transcripts are fully
synthetic). Keep it that way: don't introduce a scenario that reads live data.

---

## How determinism is guaranteed

A snapshot is only useful if the same input always renders the same bytes. The harness
enforces that:

- **Fixed width.** The recording console is a fixed width (≥ the 75-char panel), so the
  panel and its footnote wrapping are reproduced identically regardless of your real
  terminal size.
- **Pinned clock.** The scraping `Spinner` picks its frame from the clock; the capture
  console's clock is pinned so it always renders the same frame.
- **No live animation.** For RUN, Rich's live-refresh loop is stubbed out, so the strategy
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

## The two test files (what actually asserts)

- **`test_ui_snapshots.py`** — for every scenario: render it, compare to its golden file
  (or write it under `UPDATE_SNAPSHOTS=1`). Also checks that scenario keys are unique and
  that no orphan golden files exist.
- **`test_ui_colors.py`** — asserts every scenario resolves to a *valid* border color, and
  pins a curated, representative set to a *specific* expected color (one per color-decision
  branch, per surface). This guards the color logic directly, independent of the snapshot
  header.

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
