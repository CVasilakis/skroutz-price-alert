"""Fixed runtime constants: paths and the tuned execution-policy numbers.

Values that are the same for every install and are not user-configurable. Anything
a user may change is a setting resolved from ``config/`` instead, so a number
appearing here is a deliberate statement that it is not theirs to tune.

The pacing constants below are the project's politeness contract with the stores
it reads and are not performance parameters — see the scraping-practices section
of ``CLAUDE.md`` before changing any of them.
"""

import os

# --- Base Directory Paths ---
BASE_DIR: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR: str = os.path.join(BASE_DIR, "config")
STATE_DIR: str = os.path.join(BASE_DIR, "state")
LOGS_DIR: str = os.path.join(BASE_DIR, "logs")

# --- Notification Configuration ---

# Unconfigured Apprise placeholders to ignore during URL validation
APPRISE_PLACEHOLDERS: list[str] = [
    "<token>",
    "<bot_token>",
    "<chat_id>",
    "<webhook_id>",
    "<webhook_token>",
]

# --- Item Execution Policy ---

# Maximum number of times to retry scraping an item if the request fails
MAX_RETRIES: int = 3

# Number of hours after which an item check is considered old, triggering a stale warning
STALE_ITEM_HOURS: int = 48

# Base delay in seconds between processing each item to avoid rate limits
MIN_DELAY_SECONDS: int = 20

# Minimum random time in seconds added to the base delay (jitter) to simulate human behavior
RANDOM_DELAY_MIN: float = 1.0

# Maximum random time in seconds added to the base delay (jitter) to simulate human behavior
RANDOM_DELAY_MAX: float = 5.0

# Multiplier used to increase the wait time linearly on each retry attempt
RETRY_DELAY_MULTIPLIER: int = 3

# --- Infrastructure Locking ---

# Timeout in seconds when trying to acquire the file lock (0 means fail immediately if locked)
LOCK_TIMEOUT: int = 0
