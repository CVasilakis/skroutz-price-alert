"""Guard test for the load-bearing import-light contract.

Plugin discovery imports every plugin's descriptor (``plugin.py`` + package
``__init__``) merely to enumerate scrapers (argparse flags, ``list_plugins``,
``--status``). That path must NOT pull in any transport/parsing library - those belong
behind the deferred ``get_client_class`` / ``get_storage_class`` imports. This test runs
discovery in a fresh subprocess and asserts none of the known-heavy modules were loaded,
turning the contract from prose into a check.
"""

import os
import sys
import subprocess
import unittest

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))

# Run discovery in a clean interpreter, then report any heavy module that leaked in.
_SNIPPET = r"""
import sys
from core.scrapers.registry import ScraperRegistry
ScraperRegistry.registered_targets()  # triggers full plugin discovery
heavy = [m for m in ("tls_client", "selenium", "lxml") if m in sys.modules]
if heavy:
    sys.stderr.write("heavy modules imported during discovery: " + ", ".join(heavy))
    sys.exit(1)
sys.exit(0)
"""


class TestImportLight(unittest.TestCase):
    def test_discovery_does_not_import_transport_libraries(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = _SRC + os.pathsep + env.get("PYTHONPATH", "")
        result = subprocess.run(
            [sys.executable, "-c", _SNIPPET],
            env=env, capture_output=True, text=True,
        )
        self.assertEqual(
            result.returncode, 0,
            msg=f"import-light contract violated.\nstdout={result.stdout}\nstderr={result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
