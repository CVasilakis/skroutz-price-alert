"""Guard tests for the repo-level conventions a plugin must ship with.

Some plugin obligations live outside the descriptor contract the registry can
validate — notably the ``config/<config filename>.example`` template that
``install.sh`` and ``schedule.sh`` point users at ("Copy config/X.example to
config/X"). Nothing at runtime enforces it, so this turns the convention into
a check: a new store that forgets its example config fails CI instead of
shipping a dangling instruction.
"""

import os
import unittest

from constants import CONFIG_DIR
from scrapers.registry import ScraperRegistry


class TestExampleConfigConvention(unittest.TestCase):
    def test_every_plugin_ships_an_example_config(self):
        for target in ScraperRegistry.registered_targets():
            filename = ScraperRegistry.get_plugin(target).get_config_filename()
            example = os.path.join(CONFIG_DIR, filename + ".example")
            with self.subTest(target=target):
                self.assertTrue(
                    os.path.isfile(example),
                    f"Plugin '{target}' declares config '{filename}' but ships no "
                    f"config/{filename}.example template - install.sh and schedule.sh "
                    f"tell users to copy it.",
                )


if __name__ == "__main__":
    unittest.main()
