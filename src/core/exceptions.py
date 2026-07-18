class ScraperError(Exception):
    """Base exception for scraping related errors."""
    pass

class RateLimitError(ScraperError):
    """Raised when the scraper is rate limited or blocked."""
    pass

class ServerError(ScraperError):
    """Raised when the server returns a 5xx error."""
    pass

class ScraperParseError(ScraperError):
    """Raised when the scraper fails to parse the response data."""
    pass

class InvalidScrapeResultError(ScraperParseError):
    """Raised when a scraper returns a value that violates result invariants."""
    pass

class ProductNotFoundError(ScraperError):
    """Raised when a product is not found or has been removed."""
    pass

class ProductUnavailableError(ScraperError):
    """Raised when a product is found but has no price available."""
    pass

class InvalidURLError(ScraperError):
    """Raised when the provided URL is invalid or unparsable."""
    pass

class EnvFileError(Exception):
    """Raised when there is an issue with the environment configuration."""
    pass

class StorageFileError(Exception):
    """Raised when there is an issue with a storage data file."""
    pass

class ConfigFileError(StorageFileError):
    """Raised when strict user configuration cannot be loaded."""
    pass

class StateFileError(StorageFileError):
    """Raised when machine-owned state cannot be loaded or persisted."""
    pass

class UpdateCheckError(Exception):
    """Raised when there is an issue checking for script updates."""
    pass

class LockAcquisitionError(Exception):
    """Raised when a lock cannot be acquired because it is held by another process."""
    pass

class PluginError(Exception):
    """Base class for plugin discovery, validation, and dependency failures."""
    pass

class PluginDiscoveryError(PluginError):
    """Raised when a scraper plugin package cannot be discovered or imported."""
    pass

class PluginValidationError(PluginError):
    """Raised when a discovered plugin definition violates its contract."""
    pass

class PluginDependencyError(PluginError):
    """Raised when a lazily loaded plugin dependency is not installed."""
    pass
