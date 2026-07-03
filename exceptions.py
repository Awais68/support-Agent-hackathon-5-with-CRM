"""Centralized exception definitions for structured error handling."""

import os
import re
import traceback
from typing import Any, Dict, Optional


class AppError(Exception):
    """Base application error."""

    def __init__(
        self,
        message: str = "An unexpected error occurred",
        status_code: int = 500,
        error_code: str = "INTERNAL_ERROR",
        details: Optional[Dict[str, Any]] = None,
    ):
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.details = details or {}
        super().__init__(self.message)


class DatabaseError(AppError):
    """Database operation failed."""

    def __init__(self, message: str = "Database operation failed", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            status_code=500,
            error_code="DATABASE_ERROR",
            details=details,
        )


class NotFoundError(AppError):
    """Resource not found."""

    def __init__(self, message: str = "Resource not found", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            status_code=404,
            error_code="NOT_FOUND",
            details=details,
        )


class ValidationError(AppError):
    """Input validation failed."""

    def __init__(self, message: str = "Validation failed", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            status_code=400,
            error_code="VALIDATION_ERROR",
            details=details,
        )


class ConfigurationError(AppError):
    """System configuration error."""

    def __init__(self, message: str = "Configuration error", details: Optional[Dict[str, Any]] = None):
        super().__init__(
            message=message,
            status_code=500,
            error_code="CONFIGURATION_ERROR",
            details=details,
        )


class ExternalServiceError(AppError):
    """External service (OpenAI, Kafka, Twilio, Gmail) failed."""

    def __init__(self, message: str = "External service error", service: str = "unknown", details: Optional[Dict[str, Any]] = None):
        _details = dict(details or {})
        _details["service"] = service
        super().__init__(
            message=message,
            status_code=502,
            error_code="EXTERNAL_SERVICE_ERROR",
            details=_details,
        )


SENSITIVE_PATTERNS = [
    (r'(postgresql|mysql|mongodb)://[^@\s]+:[^@\s]+@', lambda m: m.group(0).split(":")[0] + "://****:****@"),
    (r'(postgresql|mysql|mongodb)://[^@\s]+@', lambda m: m.group(0).split("://")[0] + "://****@"),
    (r'(api[_-]?key|secret|token|password|apikey)\s*[:=]\s*\S{4,}', lambda m: m.group(1) + "=***"),
    (r'/[a-zA-Z]{2,}/[a-zA-Z0-9_/.-]{10,}', '/***/'),
]


def sanitize_error_message(msg: str) -> str:
    """Remove sensitive information (connection strings, keys, paths) from error messages."""
    if not isinstance(msg, str):
        return str(msg)
    for pattern, replacement in SENSITIVE_PATTERNS:
        if callable(replacement):
            msg = re.sub(pattern, replacement, msg, flags=re.IGNORECASE)
        else:
            msg = re.sub(pattern, replacement, msg, flags=re.IGNORECASE)
    return msg


def to_error_response(exc: Exception, include_traceback: bool = False) -> Dict[str, Any]:
    """Convert an exception to a structured error response dict (safe for client exposure).

    Never includes raw exception strings that could leak sensitive info.
    """
    if isinstance(exc, AppError):
        resp: Dict[str, Any] = {
            "error": exc.error_code,
            "message": exc.message,
        }
        if exc.details:
            safe_details = {k: sanitize_error_message(str(v)) if isinstance(v, str) else v for k, v in exc.details.items()}
            resp["details"] = safe_details
    else:
        resp = {
            "error": "INTERNAL_ERROR",
            "message": "An unexpected error occurred",
        }

    if include_traceback and os.getenv("DEBUG", "").lower() in ("1", "true", "yes"):
        resp["traceback"] = traceback.format_exc()

    return resp
