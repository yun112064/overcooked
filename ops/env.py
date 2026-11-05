"""Redact credentials from operational errors before displaying them."""
import re


_URL_CREDENTIALS = re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)[^\s/@\"']+@")
_BEARER = re.compile(r"\bBearer\s+\S+", re.IGNORECASE)
_KEY_VALUE = re.compile(
    r"(?i)(?P<key>password|passwd|pwd|token|secret|api[_-]?key|authorization|access[_-]?key)"
    r"(?P<sep>\s*[:=]\s*)(?P<value>\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)


def safe_error(exc: BaseException) -> str:
    message = _URL_CREDENTIALS.sub(lambda match: match["scheme"] + "***@", str(exc))
    message = _BEARER.sub("Bearer ***", message)
    message = _KEY_VALUE.sub(lambda match: match["key"] + match["sep"] + "***", message)
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__
