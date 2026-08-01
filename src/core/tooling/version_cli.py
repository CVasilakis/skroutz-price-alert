"""Machine-readable project version command for the repository-local shell interface."""

from __future__ import annotations

import sys

from core.infrastructure.updates import local_software_version


def main() -> int:
    """Print the local stable release version when one can be determined."""
    version = local_software_version()
    if version is not None:
        print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
