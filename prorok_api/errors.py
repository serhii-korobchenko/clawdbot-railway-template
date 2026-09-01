class ProrokApiError(RuntimeError):
    """Base error for the read-only PROROK API."""


class DatabaseUnavailable(ProrokApiError):
    """Raised when the PROROK SQLite database cannot be read."""
