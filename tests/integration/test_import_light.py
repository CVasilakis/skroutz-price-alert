"""Guard test for the load-bearing import-light contract.

Plugin discovery imports every plugin's descriptor (``plugin.py`` + package
``__init__``) merely to enumerate scrapers (argparse flags, ``list_plugins``,
``--status``). That path must NOT pull in any transport/parsing library - those belong
behind the deferred ``get_client_class`` / ``get_storage_class`` imports. This test runs
discovery in a fresh subprocess and enforces the contract two ways:

1. A generic check: after importing the framework contracts (the registry and the base
   plugin module - whose own dependencies, e.g. ``dotenv`` via ``core.utils``, form the
   allowed baseline), running discovery must add **no** module that lives in
   ``site-packages``. This catches any third-party import in any plugin's descriptor,
   even one whose library happens to be installed on the dev machine - the case where
   discovery would otherwise succeed silently here and then crash globally on a user
   machine that installed only another plugin.
2. The explicit known-heavy blocklist (``tls_client``, ``selenium``, ``lxml``), kept as
   a fast, readable failure message for the most likely offenders.
"""

import os
import sys
import subprocess
import unittest

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))

# Run discovery in a clean interpreter, then report any third-party module that leaked in.
_SNIPPET = r"""
import sys

# Framework baseline: the registry and the base contracts a descriptor may legitimately
# import (plus whatever *they* pull in, e.g. framework deps via core.utils).
import core.scrapers.base.plugin  # noqa: F401
from core.scrapers.registry import ScraperRegistry
baseline = set(sys.modules)

ScraperRegistry.registered_targets()  # triggers full plugin discovery

heavy = [m for m in ("tls_client", "selenium", "lxml") if m in sys.modules]
if heavy:
    sys.stderr.write("heavy modules imported during discovery: " + ", ".join(heavy))
    sys.exit(1)

# Generic check: discovery must add no module that lives in site-packages. Anything a
# descriptor imports beyond the baseline must be stdlib or project code.
third_party = sorted({
    name.split(".")[0]
    for name in set(sys.modules) - baseline
    if "site-packages" in ((getattr(sys.modules.get(name), "__file__", "") or "").replace("\\", "/"))
})
if third_party:
    sys.stderr.write(
        "third-party modules imported during discovery (a plugin descriptor imports "
        "them at module top instead of behind get_client_class/get_storage_class): "
        + ", ".join(third_party)
    )
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
