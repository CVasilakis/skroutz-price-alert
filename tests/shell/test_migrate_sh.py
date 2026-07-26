import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _sandboxed_migrate_script(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    scripts = project / "scripts"
    libraries = scripts / "lib"
    python = project / "venv" / "bin" / "python3"
    libraries.mkdir(parents=True)
    python.parent.mkdir(parents=True)
    (project / "src").mkdir()
    shutil.copy(ROOT / "scripts" / "migrate.sh", scripts / "migrate.sh")
    shutil.copy(ROOT / "scripts" / "lib" / "common.sh", libraries / "common.sh")
    shutil.copy(ROOT / "scripts" / "lib" / "preflight.sh", libraries / "preflight.sh")
    python.write_text(
        """#!/bin/sh
case "${1:-}" in
    -c)
        case "${2:-}" in
            *print*) printf '%s\\n' "3.12.0" ;;
        esac
        exit 0
        ;;
    -m)
        printf 'PYTHONPATH=%s\\n' "${PYTHONPATH:-}"
        for argument in "$@"; do
            printf 'ARG=%s\\n' "$argument"
        done
        printf '%s\\n' "forwarded stderr" >&2
        exit "${FAKE_MIGRATION_EXIT:-0}"
        ;;
esac
exit 1
""",
        encoding="utf-8",
    )
    python.chmod(0o755)
    return scripts / "migrate.sh"


def test_migrate_wrapper_forwards_environment_arguments_and_exit_status(tmp_path):
    script = _sandboxed_migrate_script(tmp_path)
    project = script.parents[1]
    env = os.environ.copy()
    env["FAKE_MIGRATION_EXIT"] = "19"

    result = subprocess.run(
        [str(script), "--machine", "--check"],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 19
    assert result.stdout.splitlines() == [
        f"PYTHONPATH={project / 'src'}",
        "ARG=-m",
        "ARG=core.tooling.migration_cli",
        "ARG=--root",
        f"ARG={project}",
        "ARG=--machine",
        "ARG=--check",
    ]
    assert result.stderr == "forwarded stderr\n"
