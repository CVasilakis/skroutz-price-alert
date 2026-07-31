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


class ResourceNotFoundError(ScraperError):
    """Raised when a requested resource is not found or has been removed."""

    pass


class PriceUnavailableError(ScraperError):
    """Raised when a resource is found but has no price available."""

    pass


class InvalidURLError(ScraperError):
    """Raised when the provided URL is invalid or unparsable."""

    pass


class StorageFileError(Exception):
    """A concise storage failure paired with optional technical diagnostics.

    ``str(error)`` is deliberately presentation-safe. Callers may write
    :attr:`diagnostic_detail` to an error log, but must not place it in a panel.
    """

    def __init__(self, display_message: str, diagnostic_detail: str | None = None) -> None:
        if not isinstance(display_message, str) or not display_message.strip():
            raise ValueError("storage display message must be nonblank")
        if diagnostic_detail is not None and (
            not isinstance(diagnostic_detail, str) or not diagnostic_detail.strip()
        ):
            raise ValueError("storage diagnostic detail must be nonblank when provided")
        self.display_message = display_message.strip()
        self.diagnostic_detail = diagnostic_detail.strip() if diagnostic_detail else None
        super().__init__(self.display_message)


class ConfigFileError(StorageFileError):
    """Raised when strict user configuration cannot be loaded."""

    pass


class StateFileError(StorageFileError):
    """Raised when machine-owned state cannot be loaded or persisted."""

    pass


class LockStorageError(StorageFileError):
    """Raised when machine-owned cooperative lock storage cannot be used."""

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
