import os

# --- Exit Codes ---
# Used to indicate failure states when running as a background service.
EXIT_CODE_SUCCESS: int = 0
EXIT_CODE_ERROR: int = 1
EXIT_CODE_INTERRUPT: int = 130  # Script was interrupted (user or system termination)
EXIT_CODE_PRODUCTS_ERROR: int = (
    15  # Issue with a plugin's products config file (e.g. config/skroutz.json)
)
EXIT_CODE_NOTIFICATION_CONFIG_ERROR: int = 16  # Unusable notification configuration
EXIT_CODE_RATE_LIMIT_ERROR: int = 17  # Blocked by server due to rate limits
EXIT_CODE_SCRAPE_ERROR: int = 18  # Parser failure or unexpected scraper fault
EXIT_CODE_STORAGE_ERROR: int = 19  # Scraper state could not be loaded or persisted
EXIT_CODE_NOTIFICATION_ERROR: int = 20  # A configured notification failed to deliver
EXIT_CODE_PLUGIN_DEPENDENCY_ERROR: int = 21  # A selected scraper's dependencies are missing
EXIT_CODE_SKIPPED: int = 42  # Skipped execution (another instance running)

# --- Base Directory Paths ---
BASE_DIR: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR: str = os.path.join(BASE_DIR, "config")
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
OLD_ENTRY_HOURS: int = 48

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
