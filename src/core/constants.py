import os

# --- Exit Codes ---
# Used to indicate failure states when running as a background service.
EXIT_CODE_SUCCESS: int = 0
EXIT_CODE_ERROR: int = 1
EXIT_CODE_INTERRUPT: int = 130        # Script was interrupted (user or system termination)
EXIT_CODE_PRODUCTS_ERROR: int = 15    # Issue with a plugin's products config file (e.g. config/skroutz.json)
EXIT_CODE_ENV_ERROR: int = 16         # Issue with the .env file
EXIT_CODE_RATE_LIMIT_ERROR: int = 17  # Blocked by server due to rate limits
EXIT_CODE_SCRAPE_ERROR: int = 18      # Parser failure or unexpected scraper fault
EXIT_CODE_STORAGE_ERROR: int = 19     # Scraped state could not be persisted
EXIT_CODE_NOTIFICATION_ERROR: int = 20  # A configured notification failed to deliver
EXIT_CODE_PLUGIN_DEPENDENCY_ERROR: int = 21  # A selected scraper's dependencies are missing
EXIT_CODE_SKIPPED: int = 42           # Skipped execution (another instance running)

# --- Base Directory Paths ---
BASE_DIR: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR: str = os.path.join(BASE_DIR, "config")
LOGS_DIR: str = os.path.join(BASE_DIR, "logs")

# --- Scraping Configuration ---

# Unconfigured Apprise placeholders to ignore during URL validation
APPRISE_PLACEHOLDERS: list[str] = ['<token>', '<bot_token>', '<chat_id>', '<webhook_id>', '<webhook_token>']

# Maximum number of times to retry scraping a product if the request fails
MAX_RETRIES: int = 3

# Number of hours after which a product check is considered old, triggering a stale warning
OLD_ENTRY_HOURS: int = 48

# Base delay in seconds between processing each product to avoid rate limits
MIN_DELAY_SECONDS: int = 20

# Minimum random time in seconds added to the base delay (jitter) to simulate human behavior
RANDOM_DELAY_MIN: float = 1.0

# Maximum random time in seconds added to the base delay (jitter) to simulate human behavior
RANDOM_DELAY_MAX: float = 5.0

# Timeout in seconds when trying to acquire the file lock (0 means fail immediately if locked)
LOCK_TIMEOUT: int = 0

# Multiplier used to increase the wait time exponentially on each retry attempt
RETRY_DELAY_MULTIPLIER: int = 3

# Timestamp format used for serializing and deserializing last_checked dates
TIMESTAMP_FORMAT: str = "%d-%m-%Y %H:%M:%S"
