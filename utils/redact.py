"""Redaction helpers for values that may end up in logs."""

from urllib.parse import urlsplit, urlunsplit


def redact_dsn(url: str | None) -> str:
    """Return a connection URL with its password replaced by ``***``.

    Keeps scheme, user, host, port, database and query so the log line is
    still useful. Anything that does not parse as a URL is returned as is.
    """
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.netloc or parts.password is None:
        return url
    userinfo, _, hostinfo = parts.netloc.rpartition("@")
    user = userinfo.split(":", 1)[0]
    return urlunsplit(parts._replace(netloc=f"{user}:***@{hostinfo}"))
