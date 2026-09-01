"""Domain errors raised by the service layer.

Services stay independent of FastAPI: they raise these, and `main.py` registers
handlers that translate them into HTTP responses. That keeps the "what went
wrong" decision in the business logic and the "which status code" decision in
the HTTP layer.
"""


class AppError(Exception):
    """Base class for expected, user-facing failures."""

    status_code = 400

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ValidationError(AppError):
    """Input the user can fix (wrong file type, too large, empty field)."""
    status_code = 400


class NotFoundError(AppError):
    status_code = 404


class PermissionError_(AppError):
    status_code = 403


class UpstreamError(AppError):
    """A third-party call failed (AI provider, scraper source)."""
    status_code = 502


class ConfigurationError(AppError):
    """The server is missing configuration the request needs."""
    status_code = 503
