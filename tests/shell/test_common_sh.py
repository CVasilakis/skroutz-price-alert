"""Behavioral tests for the POSIX helpers in scripts/lib/common.sh.

The shell layer carries real logic now — the tab-stream parsing behind the
systemd unit generation, the unit-file round-trip, the teardown target union —
so it gets the same regression safety as the Python code. Each test runs a
small `sh -eu` script in a subprocess that sources the library (exactly as the
management scripts do), optionally redefines the enumeration helpers as stubs
(plain POSIX function redefinition), and asserts on output/exit code. No
systemd and no network are involved; `SYSTEMD_USER_DIR` is redirected into a
tmp directory via ``XDG_CONFIG_HOME``.
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from core.exit_status import ExitStatus

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMON_SH = REPO_ROOT / "scripts" / "lib" / "common.sh"
SYSTEMD_SH = REPO_ROOT / "scripts" / "lib" / "systemd.sh"
_COLOR_SEQUENCE = re.compile(r"\x1b\[[0-9;]*m")


def _visible(output: str) -> str:
    """Return what the terminal shows: the output without its color sequences."""
    return _COLOR_SEQUENCE.sub("", output)


def test_install_does_not_invoke_configuration_migration():
    install = (REPO_ROOT / "scripts/install.sh").read_text(encoding="utf-8")
    assert "catalog_cli migration" not in install


def test_shared_shell_statuses_match_the_python_protocol():
    result = run_sh(
        'printf "%s %s %s" "$EXIT_STATUS_TARGET_CONFIG_ERROR" '
        '"$EXIT_STATUS_NOTIFICATION_CONFIG_ERROR" "$EXIT_STATUS_STORAGE_ERROR"'
    )

    assert result.returncode == 0
    assert result.stdout == (
        f"{int(ExitStatus.TARGET_CONFIG_ERROR)} "
        f"{int(ExitStatus.NOTIFICATION_CONFIG_ERROR)} "
        f"{int(ExitStatus.STORAGE_ERROR)}"
    )


def run_sh(script: str, base_dir=REPO_ROOT, xdg_config_home=None, extra_env=None):
    """Runs `script` under `sh -eu` with common.sh sourced (the caller contract).

    Args:
        script: The shell snippet to run after the library is sourced.
        base_dir: The BASE_DIR to expose (the sourcing contract requires it).
        xdg_config_home: When set, exported so SYSTEMD_USER_DIR lands in a tmp dir.
        extra_env: Overrides applied on top of the inherited environment before
            sourcing; a value of None *removes* the variable (so the color-guard
            tests are immune to NO_COLOR/CLICOLOR_FORCE in the developer's shell).

    Returns:
        subprocess.CompletedProcess: With captured text stdout/stderr.
    """
    env = os.environ.copy()
    if xdg_config_home is not None:
        env["XDG_CONFIG_HOME"] = str(xdg_config_home)
    for key, value in (extra_env or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    full = f'BASE_DIR="{base_dir}"\n. "{COMMON_SH}"\n. "{SYSTEMD_SH}"\n{script}'
    return subprocess.run(
        ["sh", "-eu", "-c", full],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


class TestColorGuard(unittest.TestCase):
    """The color variables are set at source time: on for a TTY (or when forced),
    empty when piped or when NO_COLOR is set - so redirected output (logs, tee,
    the systemd journal) never captures escape sequences."""

    RED_SEQ = r"\033[0;31m"  # the literal characters common.sh assigns

    def _red(self, **env):
        # capture_output pipes stdout, so [ -t 1 ] is false in every test here.
        return run_sh(
            'printf %s "$RED"',
            extra_env={
                "NO_COLOR": None,
                "CLICOLOR_FORCE": None,
                **env,
            },
        ).stdout

    def test_piped_output_is_colorless(self):
        self.assertEqual(self._red(), "")

    def test_clicolor_force_keeps_colors_on_a_pipe(self):
        # The snapshot harness relies on this to capture colored transcripts.
        self.assertEqual(self._red(CLICOLOR_FORCE="1"), self.RED_SEQ)

    def test_no_color_wins_over_force(self):
        self.assertEqual(self._red(NO_COLOR="1", CLICOLOR_FORCE="1"), "")


class TestPresentationHelpers(unittest.TestCase):
    def test_command_text_is_bold_cyan_only_when_color_is_enabled(self):
        colored = run_sh(
            "command_text './scrooge-alert status'",
            extra_env={"NO_COLOR": None, "CLICOLOR_FORCE": "1"},
        )
        plain = run_sh(
            "command_text './scrooge-alert status'",
            extra_env={"NO_COLOR": "1", "CLICOLOR_FORCE": "1"},
        )

        self.assertEqual(colored.stdout, "\x1b[1;36m./scrooge-alert status\x1b[0m")
        self.assertEqual(plain.stdout, "./scrooge-alert status")

    def test_sections_statuses_and_spacing_are_source_silent_and_colorless(self):
        result = run_sh(
            "begin_operational_output\n"
            'section_heading success "Ready"\n'
            'section_heading warning "Attention"\n'
            'task_status success "Completed"\n'
            'task_status failure "Failed"\n'
            'task_status info "Details"\n'
            'task_status warning "Caution"\n'
            "end_operational_output",
            extra_env={"NO_COLOR": "1"},
        )
        self.assertEqual(
            result.stdout,
            "\n[+] Ready\n[!] Attention\n"
            "    [v] Completed\n"
            "    [x] Failed\n"
            "    [i] Details\n"
            "    [!] Caution\n\n",
        )

    def test_tasks_guidance_and_bullets_use_four_and_eight_space_indentation(self):
        result = run_sh(
            "COLUMNS=24\n"
            'task_status info "one two three four five six"\n'
            'guidance "one two three four five six"\n'
            'bullet "one two three four five six"'
        )
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "    [i] one two three",
                "        four five six",
                "    one two three four",
                "        five six",
                "    - one two three four",
                "        five six",
            ],
        )

    def test_wrapping_defaults_to_snapshot_harness_width(self):
        message = " ".join(["word"] * 19)
        for columns in (None, "invalid"):
            with self.subTest(columns=columns):
                result = run_sh(
                    f'guidance "{message}"',
                    extra_env={"COLUMNS": columns},
                )
                self.assertEqual(result.stdout, f"    {message}\n")

    def test_wrapping_never_narrows_past_the_readable_lower_bound(self):
        """An implausibly narrow width must not degenerate to one word per line."""
        result = run_sh('guidance "one two three four five"', extra_env={"COLUMNS": "5"})

        self.assertEqual(
            result.stdout.splitlines(),
            ["    one two three", "        four five"],
        )

    def test_wrapping_measures_visible_width_not_color_sequences(self):
        """Colored prefixes and command_text must not steal columns from prose."""
        message = 'Run $(command_text "./scrooge-alert status") to check it now'
        colored = run_sh(
            f'COLUMNS=60\ntask_status warning "{message}"',
            extra_env={"NO_COLOR": None, "CLICOLOR_FORCE": "1"},
        )
        plain = run_sh(
            f'COLUMNS=60\ntask_status warning "{message}"',
            extra_env={"NO_COLOR": "1", "CLICOLOR_FORCE": "1"},
        )

        self.assertEqual(
            plain.stdout.splitlines(),
            ["    [!] Run ./scrooge-alert status to check it now"],
        )
        self.assertEqual(_visible(colored.stdout).splitlines(), plain.stdout.splitlines())


class TestDebugExecution(unittest.TestCase):
    COMMAND = (
        "probe() {\n"
        "  printf '%s\\n' stdout-line\n"
        "  printf '%s\\n' stderr-line >&2\n"
        "  return 23\n"
        "}\n"
    )

    def test_action_is_quiet_normally_and_preserves_failure_status(self):
        result = run_sh(
            self.COMMAND
            + "if run_action probe; then status=0; else status=$?; fi\n"
            + 'printf "status=%s\\n" "$status"'
        )
        self.assertEqual((result.stdout, result.stderr), ("status=23\n", ""))

    def test_action_streams_both_channels_in_debug(self):
        result = run_sh(
            self.COMMAND
            + "DEBUG_MODE=1\n"
            + "if run_action probe; then status=0; else status=$?; fi\n"
            + 'printf "status=%s\\n" "$status"'
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "stdout-line\nstatus=23\n")
        self.assertEqual(result.stderr, "stderr-line\n")

    def test_capture_keeps_stdout_quiet_diagnostics_and_exact_status(self):
        result = run_sh(
            self.COMMAND
            + "if run_captured probe; then status=0; else status=$?; fi\n"
            + 'printf "stdout=%s stderr=%s status=%s\\n" '
            + '"$CAPTURED_COMMAND_OUTPUT" "$CAPTURED_COMMAND_STDERR" "$status"'
        )
        self.assertEqual(result.stdout, "stdout=stdout-line stderr=stderr-line status=23\n")
        self.assertEqual(result.stderr, "")

    def test_debug_capture_mirrors_both_channels_without_double_execution(self):
        temp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)
        counter = temp_dir / "counter"
        result = run_sh(
            "probe() {\n"
            f'  printf x >> "{counter}"\n'
            "  printf '%s\\n' stdout-line\n"
            "  printf '%s\\n' stderr-line >&2\n"
            "  return 37\n"
            "}\n"
            "DEBUG_MODE=1\n"
            "if run_captured probe; then status=0; else status=$?; fi\n"
            'printf "captured=%s status=%s\\n" "$CAPTURED_COMMAND_OUTPUT" "$status"',
            extra_env={"TMPDIR": str(temp_dir)},
        )
        self.assertEqual(
            result.stdout,
            "captured=stdout-line status=37\n",
        )
        self.assertEqual(result.stderr, "stdout-line\nstderr-line\n")
        self.assertEqual(counter.read_text(), "x")
        self.assertEqual(list(temp_dir.glob("scrooge-capture.*")), [])

    def test_capture_workspace_is_removed_after_success(self):
        temp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)
        result = run_sh(
            "run_captured printf output",
            extra_env={"TMPDIR": str(temp_dir)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(temp_dir.iterdir()), [])


class TestProgressExecution(unittest.TestCase):
    def test_lock_contention_is_bounded_and_filesystem_errors_fail_immediately(self):
        temp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)
        stale_workspace = temp_dir / "stale"
        stale_workspace.mkdir()
        (stale_workspace / "lock").mkdir()
        failed_workspace = temp_dir / "failed"
        failed_workspace.mkdir()

        stale = run_sh(
            "attempts=0\n"
            "mkdir() { attempts=$((attempts + 1)); return 1; }\n"
            f'if _progress_lock "{stale_workspace}"; then status=0; else status=$?; fi\n'
            'printf "attempts=%s status=%s\\n" "$attempts" "$status"'
        )
        failed = run_sh(
            "attempts=0\n"
            "mkdir() { attempts=$((attempts + 1)); return 1; }\n"
            f'if _progress_lock "{failed_workspace}"; then status=0; else status=$?; fi\n'
            'printf "attempts=%s status=%s\\n" "$attempts" "$status"'
        )

        self.assertEqual(stale.stdout, "attempts=20 status=1\n")
        self.assertEqual(failed.stdout, "attempts=1 status=1\n")

    def test_a_failed_yield_abandons_the_lock_instead_of_spinning(self):
        """A yield that cannot run (fork failure, or a signal reaching the shell)
        must stop the retry loop rather than burn the remaining attempts. The
        contended workspace is what makes this observable: the same input retries
        twenty times when the yield succeeds, so a single attempt can only come
        from the failed yield."""
        temp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)
        contended_workspace = temp_dir / "contended"
        contended_workspace.mkdir()
        (contended_workspace / "lock").mkdir()

        result = run_sh(
            "attempts=0\n"
            "mkdir() { attempts=$((attempts + 1)); return 1; }\n"
            "sleep() { return 1; }\n"
            f'if _progress_lock "{contended_workspace}"; then\n'
            "  status=0\n"
            "else\n"
            "  status=$?\n"
            "fi\n"
            'printf "attempts=%s status=%s\\n" "$attempts" "$status"'
        )

        self.assertEqual(result.stdout, "attempts=1 status=1\n")

    def test_redirected_output_uses_permanent_line_and_preserves_status(self):
        result = run_sh(
            "probe() {\n"
            "  PROBE_SIDE_EFFECT=preserved\n"
            "  return 23\n"
            "}\n"
            "PROBE_SIDE_EFFECT=missing\n"
            'if run_with_progress "Running probe..." probe; then\n'
            "  status=0\n"
            "else\n"
            "  status=$?\n"
            "fi\n"
            'printf "side-effect=%s status=%s\\n" "$PROBE_SIDE_EFFECT" "$status"'
        )
        self.assertEqual(
            result.stdout,
            "    [i] Running probe...\nside-effect=preserved status=23\n",
        )
        self.assertEqual(result.stderr, "")

    def test_debug_mode_leaves_the_command_in_charge_of_output(self):
        result = run_sh(
            "DEBUG_MODE=1\n"
            "probe() { printf '%s\\n' command-output; }\n"
            'run_with_progress "Running probe..." probe'
        )
        self.assertEqual(result.stdout, "command-output\n")
        self.assertEqual(result.stderr, "")

    def test_progress_setup_failure_falls_back_without_changing_command(self):
        temp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)
        workspace = temp_dir / "prepared"
        workspace.mkdir()
        (workspace / "active").mkdir()
        result = run_sh(
            "_progress_capabilities() {\n"
            "  PROGRESS_CURSOR_UP='<UP>'\n"
            "  PROGRESS_CARRIAGE_RETURN='<CR>'\n"
            "  PROGRESS_ERASE_LINE='<EL>'\n"
            "  PROGRESS_COLUMNS=100\n"
            "  return 0\n"
            "}\n"
            f'mktemp() {{ printf "%s\\n" "{workspace}"; }}\n'
            "probe() { PROBE_SIDE_EFFECT=preserved; return 29; }\n"
            "PROBE_SIDE_EFFECT=missing\n"
            'if run_with_progress "Running probe..." probe; then\n'
            "  status=0\n"
            "else\n"
            "  status=$?\n"
            "fi\n"
            'printf "side-effect=%s status=%s\\n" "$PROBE_SIDE_EFFECT" "$status"',
            extra_env={"TMPDIR": str(temp_dir)},
        )
        self.assertEqual(
            result.stdout,
            "    [i] Running probe...\nside-effect=preserved status=29\n",
        )
        self.assertEqual(result.stderr, "")
        self.assertFalse(workspace.exists())

    def test_process_cleanup_stops_waiting_after_the_fixed_attempt_limit(self):
        temp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)
        ready = temp_dir / "ready"
        child_pid = temp_dir / "child-pid"
        result = run_sh(
            "(\n"
            "  trap '' TERM\n"
            "  sleep 30 &\n"
            "  stubborn_child=$!\n"
            f'  printf "%s\\n" "$stubborn_child" > "{child_pid}"\n'
            f'  : > "{ready}"\n'
            '  wait "$stubborn_child"\n'
            ") >/dev/null 2>&1 &\n"
            "stubborn_pid=$!\n"
            f'while [ ! -f "{ready}" ]; do sleep 0; done\n'
            'if _progress_stop_process "$stubborn_pid"; then\n'
            "  status=0\n"
            "else\n"
            "  status=$?\n"
            "fi\n"
            f'IFS= read -r stubborn_child < "{child_pid}"\n'
            'kill -KILL "$stubborn_pid" "$stubborn_child" 2>/dev/null || true\n'
            'wait "$stubborn_pid" 2>/dev/null || true\n'
            'printf "status=%s\\n" "$status"'
        )
        self.assertEqual(result.stdout, "status=1\n")
        self.assertEqual(result.stderr, "")

    def test_capable_fast_command_finishes_without_showing_progress(self):
        temp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)
        result = run_sh(
            "_progress_capabilities() {\n"
            "  PROGRESS_CURSOR_UP='<UP>'\n"
            "  PROGRESS_CARRIAGE_RETURN='<CR>'\n"
            "  PROGRESS_ERASE_LINE='<EL>'\n"
            "  PROGRESS_COLUMNS=100\n"
            "  return 0\n"
            "}\n"
            "progress_delay() { while :; do :; done; }\n"
            'run_with_progress "Running probe..." :\n'
            'task_status success "Probe passed."',
            extra_env={"TMPDIR": str(temp_dir)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "    [v] Probe passed.\n")
        self.assertEqual(list(temp_dir.iterdir()), [])

    def test_capable_path_still_runs_the_command_in_the_caller_shell(self):
        # The two fallback paths already pin this; the capable path is the one
        # that forks a presenter, so it is where a subshell would be introduced.
        # check.sh, plugin-check.sh, plugin-create.sh, and update.sh all layer
        # run_captured under run_with_progress and then read
        # CAPTURED_COMMAND_OUTPUT, which only reaches them because the command
        # runs in their own shell.
        temp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)
        result = run_sh(
            "_progress_capabilities() {\n"
            "  PROGRESS_CURSOR_UP='<UP>'\n"
            "  PROGRESS_CARRIAGE_RETURN='<CR>'\n"
            "  PROGRESS_ERASE_LINE='<EL>'\n"
            "  PROGRESS_COLUMNS=100\n"
            "  return 0\n"
            "}\n"
            "progress_delay() { while :; do :; done; }\n"
            "probe() { printf '%s\\n' captured-line; return 23; }\n"
            "PROBE_SIDE_EFFECT=missing\n"
            'if run_with_progress "Running probe..." run_captured probe; then\n'
            "  status=0\n"
            "else\n"
            "  status=$?\n"
            "fi\n"
            'printf "captured=%s status=%s\\n" "$CAPTURED_COMMAND_OUTPUT" "$status"',
            extra_env={"TMPDIR": str(temp_dir)},
        )
        self.assertEqual(result.stdout, "captured=captured-line status=23\n")
        self.assertEqual(result.stderr, "")
        self.assertEqual(list(temp_dir.iterdir()), [])

    def test_capable_slow_command_replaces_exactly_one_progress_line(self):
        temp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)
        shown = temp_dir / "shown"
        result = run_sh(
            "_progress_capabilities() {\n"
            "  PROGRESS_CURSOR_UP='<UP>'\n"
            "  PROGRESS_CARRIAGE_RETURN='<CR>'\n"
            "  PROGRESS_ERASE_LINE='<EL>'\n"
            "  PROGRESS_COLUMNS=100\n"
            "  return 0\n"
            "}\n"
            "progress_delay() { :; }\n"
            "task_status() {\n"
            '  printf "    [%s] %s\\n" "$1" "$2"\n'
            f'  [ "$1" != info ] || : > "{shown}"\n'
            "}\n"
            "probe() {\n"
            f'  while [ ! -f "{shown}" ]; do sleep 1; done\n'
            "}\n"
            'run_with_progress "Running probe..." probe\n'
            'task_status success "Probe passed."',
            extra_env={"TMPDIR": str(temp_dir)},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "    [info] Running probe...\n<UP><CR><EL>    [success] Probe passed.\n",
        )
        self.assertEqual(list(temp_dir.iterdir()), [shown])


class TestTargetFlagParsing(unittest.TestCase):
    def test_debug_is_recognized_anywhere_and_targets_stay_deduplicated(self):
        result = run_sh(
            "parse_target_flags --alpha --debug --beta --alpha\n"
            "printf 'debug=%s explicit=%s help=%s internal=%s\\n%s\\n' "
            '"$DEBUG_MODE" "$TARGET_FLAGS_EXPLICIT" "$TARGET_HELP_REQUESTED" '
            '"$SCROOGE_INTERNAL_DEBUG" "$TARGET_FLAGS"'
        )
        self.assertEqual(
            result.stdout.splitlines(),
            ["debug=1 explicit=1 help=0 internal=1", "alpha", "beta"],
        )

    def test_help_precedence_is_preserved_with_debug_and_invalid_arguments(self):
        result = run_sh(
            "parse_target_flags --alpha bad --debug --help --beta\n"
            "printf 'debug=%s explicit=%s help=%s targets=%s\\n' "
            '"$DEBUG_MODE" "$TARGET_FLAGS_EXPLICIT" "$TARGET_HELP_REQUESTED" "$TARGET_FLAGS"'
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "debug=1 explicit=0 help=1 targets=\n")

    def test_normal_parse_resets_inherited_internal_debug_state(self):
        result = run_sh(
            'parse_target_flags --alpha\nprintf "%s:%s" "$DEBUG_MODE" "$SCROOGE_INTERNAL_DEBUG"',
            extra_env={"SCROOGE_INTERNAL_DEBUG": "1"},
        )
        self.assertEqual(result.stdout, "0:0")


class TestNamingHelpers(unittest.TestCase):
    def test_unit_name(self):
        result = run_sh("unit_name skroutz timer")
        self.assertEqual(result.stdout, "skroutz-scraper.timer")


class TestTargetStreams(unittest.TestCase):
    """The newline-delimited ordered set the whole shell layer selects targets with.

    The prose contract sits above stream_contains in scripts/lib/common.sh; each
    test below pins one clause of it, and each names the caller that would break.
    They exist because the properties are invisible at the call sites: every
    lifecycle command asks "is this target flag in this set?" without showing that
    membership is by whole item, or that a stream can hold no empty item.
    """

    def test_stream_contains_hit_and_miss(self):
        self.assertEqual(run_sh('stream_contains b "a\nb\nc"').returncode, 0)
        self.assertEqual(run_sh('stream_contains z "a\nb\nc"').returncode, 1)

    def test_stream_contains_with_empty_list(self):
        self.assertEqual(run_sh('stream_contains z ""').returncode, 1)

    def test_stream_contains_matches_whole_items_only(self):
        """A prefix of a target is not that target, so --skro cannot select skroutz.

        select_targets and runtime_target_flag accept or reject a target flag on
        this answer alone, so a substring match would let a typo run a scraper.
        """
        self.assertEqual(run_sh('stream_contains skro "skroutz\nfoo"').returncode, 1)
        self.assertEqual(run_sh('stream_contains skroutzz "skroutz\nfoo"').returncode, 1)

    def test_stream_contains_does_not_split_items_on_spaces(self):
        """Guards the IFS-newline mechanism itself, not just this helper.

        Dropping the IFS assignment -- or indenting its closing quote, which puts
        the indentation into IFS -- would split on spaces too, and no target-name
        stream would notice. plugin_stream_value returns snapshot values that do
        contain spaces, so this is the test that fails if the mechanism is lost.
        """
        self.assertEqual(run_sh('stream_contains bar "foo bar"').returncode, 1)
        self.assertEqual(run_sh('stream_contains "foo bar" "foo bar"').returncode, 0)

    def test_stream_never_holds_an_empty_item(self):
        """Newline is IFS whitespace, so blank lines collapse instead of becoming items.

        This is what makes the empty-set rules safe: an accumulator can start at
        '' and a lookup for '' can never accidentally match a stray blank line.
        """
        result = run_sh("stream_union \"$(printf 'a\\n\\nb')\"")
        self.assertEqual(result.stdout.splitlines(), ["a", "b"])
        self.assertEqual(run_sh("stream_contains '' \"$(printf 'a\\n\\nb')\"").returncode, 1)

    def test_stream_add_unique_preserves_order_and_deduplicates(self):
        result = run_sh('items="alpha\nbeta"\nstream_add_unique "$items" beta')
        self.assertEqual(result.stdout.splitlines(), ["alpha", "beta"])

    def test_stream_add_unique_onto_an_empty_stream_adds_no_blank_line(self):
        """Every accumulator in the layer starts at '', so this is the common case."""
        result = run_sh("stream_add_unique '' alpha")
        self.assertEqual(result.stdout, "alpha\n")

    def test_stream_union_with_empty_operands(self):
        """Both operands are routinely empty at the two call sites.

        select_targets unions registered with installed, and either side is empty
        on a broken catalog or a fresh machine; list_installed_targets unions
        timers with services, and a half-installed pair leaves one of them empty.
        """
        self.assertEqual(run_sh("stream_union '' ''").stdout, "")
        self.assertEqual(run_sh("stream_union '' \"$(printf 'a\\nb')\"").stdout, "a\nb\n")
        self.assertEqual(run_sh("stream_union \"$(printf 'a\\nb')\"").stdout, "a\nb\n")

    def test_stream_for_display_joins_items_on_one_line(self):
        """Rendered verbatim into the user-facing 'Available targets:' line.

        The trailing space is the helper's shape rather than an oversight: it
        emits one 'item ' per item. It stays invisible because the terminal
        snapshots strip trailing whitespace, so removing it is not worth a change.
        """
        self.assertEqual(run_sh("stream_for_display \"$(printf 'a\\nb')\"").stdout, "a b ")
        self.assertEqual(run_sh("stream_for_display ''").stdout, "")

    def test_plugin_stream_value_preserves_spaces(self):
        result = run_sh(
            "rows=\"$(printf 'foo\\tcustom feed.json\\nbar\\tother.json')\"\n"
            'plugin_stream_value foo "$rows"'
        )
        self.assertEqual(result.stdout, "custom feed.json")

    def test_plugin_stream_value_reports_a_miss(self):
        """Callers branch on this status rather than on an empty value.

        schedule.sh, provisioning.sh, and install.sh each guard the call, because
        under set -e an unguarded assignment from a miss ends the script silently.
        """
        result = run_sh(
            "rows=\"$(printf 'foo\\tone.json')\"\n"
            'if plugin_stream_value nope "$rows"; then printf hit; else printf "miss=%s" "$?"; fi'
        )
        self.assertEqual(result.stdout, "miss=1")


class TestMetadataSnapshots(unittest.TestCase):
    def test_catalog_and_schedule_projections_each_share_one_acquisition(self):
        temp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)
        catalog_counter = temp_dir / "catalog-calls"
        schedule_counter = temp_dir / "schedule-calls"
        script = (
            "catalog_cli() {\n"
            '  case "$1" in\n'
            f'    catalog) printf x >> "{catalog_counter}"; '
            "printf 'alpha\\tAlpha Store\\t/example.json\\t/req.txt\\n' ;;\n"
            f'    schedules) printf x >> "{schedule_counter}"; '
            "printf 'alpha\\thourly\\tok\\t\\n' ;;\n"
            "  esac\n"
            "}\n"
            "load_plugin_catalog\n"
            "list_plugins >/dev/null\n"
            "plugin_display_name alpha >/dev/null\n"
            "list_plugin_examples >/dev/null\n"
            "list_plugin_requirements >/dev/null\n"
            "load_plugin_schedules\n"
            "list_plugin_schedules >/dev/null\n"
            "list_interval_status >/dev/null\n"
            "list_schedule_errors >/dev/null\n"
        )
        result = run_sh(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(catalog_counter.read_text(), "x")
        self.assertEqual(schedule_counter.read_text(), "x")


class TestEagerPriming(unittest.TestCase):
    """The eager primers exist so a --debug run can mirror the metadata commands.

    They fill the same caches the lazy loaders fill, so the mirrored output is
    also the only acquisition: every later lazy call must be served from the
    warm cache. The commands with a --debug mode all depend on this, which is
    why the contract is asserted here rather than only through their snapshots.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.counter = self.tmp / "calls"

    def _stub(self, exit_status=0):
        """A catalog_cli stub that records each call and can fail on demand."""
        return (
            "catalog_cli() {\n"
            f'  printf x >> "{self.counter}"\n'
            "  printf 'alpha\\tAlpha Store\\t/example.json\\t/req.txt\\n'\n"
            "  printf 'catalog stderr line\\n' >&2\n"
            f"  return {exit_status}\n"
            "}\n"
        )

    def test_priming_serves_every_later_lazy_call(self):
        result = run_sh(
            self._stub()
            + "DEBUG_MODE=1\n"
            + "prime_plugin_catalog\n"
            + "list_plugins >/dev/null\n"
            + "plugin_display_name alpha >/dev/null\n"
            + "load_plugin_catalog\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.counter.read_text(), "x")

    def test_a_second_prime_is_served_from_the_warm_cache(self):
        """install.sh primes twice, so the memo has to hold across both calls."""
        result = run_sh(self._stub() + "DEBUG_MODE=1\nprime_plugin_catalog\nprime_plugin_catalog\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.counter.read_text(), "x")

    def test_resetting_the_cache_re_reads_through_a_later_prime(self):
        """install.sh re-reads the same metadata through the completed venv."""
        result = run_sh(
            self._stub()
            + "DEBUG_MODE=1\nprime_plugin_catalog\nreset_catalog_cache\nprime_plugin_catalog\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.counter.read_text(), "xx")

    def test_priming_mirrors_both_streams_once_in_debug_mode(self):
        result = run_sh(
            self._stub() + "DEBUG_MODE=1\nprime_plugin_catalog\nlist_plugins >/dev/null\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.count("Alpha Store"), 1)
        self.assertEqual(result.stderr.count("catalog stderr line"), 1)

    def test_priming_is_silent_outside_debug_mode(self):
        result = run_sh(
            self._stub() + "DEBUG_MODE=0\nprime_plugin_catalog\nlist_plugins >/dev/null\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_a_failed_prime_is_reported_and_not_retried(self):
        result = run_sh(
            self._stub(exit_status=1)
            + "DEBUG_MODE=0\n"
            + "if prime_plugin_catalog; then printf 'loaded\\n'; else printf 'refused\\n'; fi\n"
            + "if list_plugins; then printf 'served\\n'; else printf 'propagated\\n'; fi\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["refused", "propagated"])
        self.assertEqual(self.counter.read_text(), "x")

    def test_both_lazy_loaders_stay_silent_on_failure(self):
        """The lazy loaders are the quiet path, so neither stream may leak.

        Every current caller primes first, so this guards the fallback the
        accessors would reach if a future caller did not.
        """
        result = run_sh(
            self._stub(exit_status=1)
            + "DEBUG_MODE=0\n"
            + "load_plugin_catalog || true\n"
            + "load_plugin_schedules || true\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_schedule_priming_serves_every_later_lazy_call(self):
        result = run_sh(
            self._stub()
            + "DEBUG_MODE=1\n"
            + "prime_plugin_schedules\n"
            + "list_plugin_schedules >/dev/null\n"
            + "list_interval_status >/dev/null\n"
            + "load_plugin_schedules\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.counter.read_text(), "x")


class TestUnitFileRoundTrip(unittest.TestCase):
    """The unit writer accepts and recovers one framework-owned calendar value."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.unit_dir = self.tmp / "systemd" / "user"

    def _write(self, plugin, calendar):
        return run_sh(
            'mkdir -p "$SYSTEMD_USER_DIR"\n'
            f'render_plugin_service {plugin} "$SYSTEMD_USER_DIR/{plugin}-scraper.service"\n'
            f'render_plugin_timer {plugin} "{calendar}" '
            f'"$SYSTEMD_USER_DIR/{plugin}-scraper.timer"',
            xdg_config_home=self.tmp,
        )

    def test_round_trip_recovers_the_trigger_block(self):
        calendar = "*-*-* 00/2:00:00"
        self.assertEqual(self._write("foo", calendar).returncode, 0)
        read = run_sh("read_timer_oncalendar foo", xdg_config_home=self.tmp)
        self.assertEqual(read.stdout, calendar)

    def test_framework_keys_are_appended_but_not_read_back(self):
        self._write("foo", "hourly")
        timer_text = (self.unit_dir / "foo-scraper.timer").read_text()
        self.assertIn("RandomizedDelaySec=180s", timer_text)
        self.assertIn("Persistent=true", timer_text)
        self.assertIn("Unit=foo-scraper.service", timer_text)
        read = run_sh("read_timer_oncalendar foo", xdg_config_home=self.tmp)
        self.assertEqual(read.stdout, "hourly")

    def test_service_dispatches_through_run_sh(self):
        self._write("foo", "hourly")
        service_text = (self.unit_dir / "foo-scraper.service").read_text()
        self.assertIn(f'ExecStart="{REPO_ROOT}/scripts/run.sh" --quiet --foo', service_text)

    def test_read_timer_calendar_symlink_does_not_read_through(self):
        """A linked timer must never compare equal to the configured schedule.

        schedule.sh skips a target whose installed calendar already matches, so
        following the link would silently accept a unit the transaction is
        required to reject, and the user would never be told to remove it.
        """
        self.assertEqual(self._write("foo", "hourly").returncode, 0)
        linked = self.unit_dir / "bar-scraper.timer"
        linked.symlink_to(self.unit_dir / "foo-scraper.timer")

        read = run_sh("read_timer_oncalendar bar", xdg_config_home=self.tmp)

        self.assertEqual((read.returncode, read.stdout), (0, ""))

    def test_read_timer_calendar_missing_unit_is_empty_success(self):
        read = run_sh("read_timer_oncalendar ghost", xdg_config_home=self.tmp)
        self.assertEqual((read.returncode, read.stdout), (0, ""))


class TestKnownTargets(unittest.TestCase):
    """The policy selector uses registered and installed snapshots consistently."""

    STUBS = (
        "list_plugins() { printf '%s\\n' skroutz amazon; }\n"
        "list_installed_units() { printf '%s\\n' amazon ghost; }\n"
        "list_installed_targets() { printf '%s\\n' amazon ghost; }\n"
    )

    def test_union_preserves_first_seen_order_and_dedups(self):
        result = run_sh(self.STUBS + 'stream_union "$(list_plugins)" "$(list_installed_targets)"')
        self.assertEqual(result.stdout.split(), ["skroutz", "amazon", "ghost"])

    def test_orphan_unit_is_a_known_target(self):
        result = run_sh(
            self.STUBS
            + "TARGET_FLAGS=ghost\nTARGET_FLAGS_EXPLICIT=1\n"
            + 'select_targets installed_union\nprintf %s "$SELECTED_TARGETS"'
        )
        self.assertEqual((result.returncode, result.stdout), (0, "ghost"))

    def test_registered_but_uninstalled_is_reported_without_selection(self):
        result = run_sh(
            self.STUBS
            + "TARGET_FLAGS=skroutz\nTARGET_FLAGS_EXPLICIT=1\n"
            + 'select_targets installed_union\nprintf "\\nSELECTED=%s\\n" "$SELECTED_TARGETS"'
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("SELECTED=", result.stdout)

    def test_typo_is_rejected(self):
        result = run_sh(
            self.STUBS
            + "TARGET_FLAGS=skrutz\nTARGET_FLAGS_EXPLICIT=1\n"
            + "select_targets installed_union"
        )
        self.assertEqual(result.returncode, 1)

    def test_installed_target_union_includes_service_only_unit(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        unit_dir = tmp / "systemd" / "user"
        unit_dir.mkdir(parents=True)
        (unit_dir / "timeronly-scraper.timer").touch()
        (unit_dir / "serviceonly-scraper.service").touch()
        result = run_sh("list_installed_targets", xdg_config_home=tmp)
        self.assertEqual(result.stdout.split(), ["timeronly", "serviceonly"])

    def test_installed_target_union_includes_dangling_symlink(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        unit_dir = tmp / "systemd" / "user"
        unit_dir.mkdir(parents=True)
        (unit_dir / "ghost-scraper.timer").symlink_to("missing.timer")
        result = run_sh("list_installed_targets", xdg_config_home=tmp)
        self.assertEqual(result.stdout.split(), ["ghost"])


class TestCatalogDiagnose(unittest.TestCase):
    """catalog_diagnose distinguishes a missing venv from failed plugin discovery."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_missing_venv_reports_reinstall_hint(self):
        # A BASE_DIR with no venv at all -> the reinstall hint, not a discovery error.
        result = run_sh("catalog_diagnose", base_dir=self.tmp)
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing or broken", result.stderr)
        self.assertIn("./scrooge-alert install", result.stderr)

    def test_broken_plugin_reports_the_real_discovery_error(self):
        # A real venv + a copied source tree containing one broken plugin package:
        # the diagnosis must surface the PluginDiscoveryError naming that package,
        # which the quiet list_* helpers would otherwise hide as an empty list.
        venv_python = REPO_ROOT / "venv" / "bin" / "python3"
        if not venv_python.exists():  # pragma: no cover - core-only checkout
            self.skipTest("project venv not available")

        shutil.copytree(REPO_ROOT / "src", self.tmp / "src")
        # Keep the project venv root real while reusing the checked-in
        # environment through normal links inside it.
        (self.tmp / "venv").mkdir()
        os.symlink(REPO_ROOT / "venv" / "bin", self.tmp / "venv" / "bin")
        broken = self.tmp / "src" / "core" / "scrapers" / "plugins" / "zzzbroken"
        broken.mkdir()
        (broken / "__init__.py").write_text('raise RuntimeError("boom")\n')

        result = run_sh("catalog_diagnose", base_dir=self.tmp)
        self.assertEqual(result.returncode, 1)
        self.assertIn("catalog could not be loaded", result.stderr)
        self.assertIn("PluginDiscoveryError", result.stderr)
        self.assertIn("zzzbroken", result.stderr)

    def test_transient_catalog_failure_reports_that_retry_is_safe(self):
        python = self.tmp / "venv" / "bin" / "python3"
        python.parent.mkdir(parents=True)
        python.write_text("#!/bin/sh\nexit 0\n")
        python.chmod(0o755)
        result = run_sh(
            "catalog_cli() {\n"
            '  [ "$1" = diagnose ] || return 1\n'
            "  printf '%s\\n' 'Plugin discovery succeeds now (2 plugins).'\n"
            "}\n"
            "catalog_diagnose",
            base_dir=self.tmp,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("catalog could not be loaded", result.stderr)
        self.assertIn("Plugin discovery succeeds now (2 plugins).", result.stderr)
        self.assertIn("catalog is readable now; retry", result.stderr)
        self.assertNotIn("Fix (or remove)", result.stderr)


class TestListSupportedIntervals(unittest.TestCase):
    def test_matches_the_settings_vocabulary(self):
        venv_python = REPO_ROOT / "venv" / "bin" / "python3"
        if not venv_python.exists():  # pragma: no cover - core-only checkout
            self.skipTest("project venv not available")
        from core.scrapers.framework.intervals import SUPPORTED_INTERVALS

        result = run_sh("list_supported_intervals")
        self.assertEqual(result.stdout.strip(), ", ".join(SUPPORTED_INTERVALS))


class TestRealRegistryBridge(unittest.TestCase):
    """The list_* helpers' real Python bodies, run through sh against the real venv.

    The UI shell snapshots fake the venv responder (canned answers), so these are
    the only place the actual Python inside common.sh's heredocs executes via the
    shell — a break in the shell<->Python contract (the tab-stream shape, the
    name/filename pairing) fails here instead of hiding behind the shim. The bridge runs
    against an empty temporary config directory so private user configuration cannot
    affect its result.
    """

    def setUp(self):
        if not (REPO_ROOT / "venv" / "bin" / "python3").exists():  # pragma: no cover
            self.skipTest("project venv not available")
        self.base_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base_dir, ignore_errors=True)
        (self.base_dir / "venv").mkdir()
        (self.base_dir / "venv" / "bin").symlink_to(
            REPO_ROOT / "venv" / "bin", target_is_directory=True
        )
        (self.base_dir / "src").symlink_to(REPO_ROOT / "src", target_is_directory=True)
        (self.base_dir / "config").mkdir()

    def test_list_plugins_yields_registered_names(self):
        result = run_sh("list_plugins", base_dir=self.base_dir)
        self.assertIn("skroutz", result.stdout.split())

    def test_list_plugin_examples_pairs_names_with_package_paths(self):
        result = run_sh("list_plugin_examples", base_dir=self.base_dir)
        pairs = dict(line.split("\t", 1) for line in result.stdout.splitlines())
        self.assertTrue(pairs.get("skroutz", "").endswith("/skroutz/config.example.json"))

    def test_catalog_ignores_malformed_config_but_schedule_reports_it(self):
        (self.base_dir / "config" / "insomnia.json").write_text('{"products": [], "settings": {}}')
        catalog = run_sh("list_plugins", base_dir=self.base_dir)
        self.assertEqual(catalog.returncode, 0, catalog.stderr)
        self.assertIn("skroutz", catalog.stdout.split())
        self.assertIn("insomnia", catalog.stdout.split())
        statuses = run_sh("list_interval_status", base_dir=self.base_dir)
        self.assertEqual(statuses.returncode, 0, statuses.stderr)
        self.assertIn("insomnia\terror", statuses.stdout.splitlines())

    def test_list_plugin_schedules_is_a_value_stream(self):
        result = run_sh("list_plugin_schedules", base_dir=self.base_dir)
        lines = result.stdout.splitlines()
        self.assertTrue(lines)
        for line in lines:
            plugin, tab, calendar = line.partition("\t")
            self.assertEqual(tab, "\t", f"no tab separator in {line!r}")
            self.assertTrue(plugin)
            self.assertTrue(calendar)
        self.assertTrue(any(line.startswith("skroutz\t") for line in lines))

    def test_list_interval_status_reports_a_known_status(self):
        result = run_sh("list_interval_status", base_dir=self.base_dir)
        statuses = dict(line.split("\t") for line in result.stdout.splitlines())
        self.assertEqual(statuses.get("skroutz"), "nocfg")

    def test_every_accessor_reports_an_unloadable_catalog(self):
        """A load failure must not reach callers as an empty success.

        Each accessor filters the loaded stream through awk, and a pipeline's
        status is its last command's -- awk succeeds on empty input. Without an
        explicit load outside the pipeline these all returned 0 with no rows,
        which is indistinguishable from "no plugin is registered" and is the
        shape callers branch on.
        """
        for accessor in (
            "list_plugins",
            "plugin_display_name skroutz",
            "list_plugin_examples",
            "list_plugin_requirements",
            "list_plugin_schedules",
            "list_interval_status",
            "list_schedule_errors",
        ):
            with self.subTest(accessor=accessor):
                result = run_sh(
                    f"reset_catalog_cache\n{accessor}",
                    base_dir=self.base_dir,
                    extra_env={"CATALOG_PYTHON": "/bin/false"},
                )
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
