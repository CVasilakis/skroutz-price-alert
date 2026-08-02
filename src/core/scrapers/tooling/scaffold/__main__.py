"""Run the plugin scaffold command-line interface."""

import sys

from core.scrapers.tooling.scaffold.cli import main

if __name__ == "__main__":
    sys.exit(main())
