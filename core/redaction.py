"""Shared helpers for safe user-facing and log-facing error text."""

from __future__ import annotations

import re


_SECRET_KEY_NAMES = r"api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|bearer|token|secret|password|passwd"
_QUOTED_SECRET_PATTERN = re.compile(
    rf"""(?isx)
    (
      ["']?
      (?:{_SECRET_KEY_NAMES})
      ["']?
      \s*[:=]\s*
    )
    (["'])
    (?:\\.|(?!\2).)*
    \2
    """
)
_SECRET_PATTERNS = [
    re.compile(r"(?i)\b((?:https?:)?//)([^\s/@]+@)"),
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?is)(authorization\s*[:=]\s*)bearer\s+([\"'])(?:\\.|(?!\2).)*\2"),
    re.compile(r"(?is)(bearer\s+)([\"'])(?:\\.|(?!\2).)*\2"),
    re.compile(r"(?i)(authorization\s*[:=]\s*)bearer\s+([^\s,;\"'}]+)"),
    re.compile(r"(?i)(bearer\s+)([^\s,;\"'}]+)"),
    re.compile(r"(?i)(api[_-]?key|authorization|bearer|token|secret|password|passwd)(\s*[:=]\s*)([^\s,;&]+)"),
]
_JSON_SECRET_PATTERN = re.compile(
    rf"""(?ix)
    (
      ["']?
      (?:{_SECRET_KEY_NAMES})
      ["']?
      \s*[:=]\s*
      ["']?
    )
    ([^\s,;"'}}\]\[]+)
    (["']?)
    """
)
_SECRET_KEY_PATTERN = re.compile(r"(?i)(api[_-]?key|authorization|bearer|token|secret|password|passwd)")


def redact_text(value: object, limit: int = 500, collapse_whitespace: bool = True) -> str:
    text = str(value).strip()
    if collapse_whitespace:
        text = text.replace("\r", " ").replace("\n", " ")
    text = _QUOTED_SECRET_PATTERN.sub(_redact_quoted_secret_match, text)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(_redact_match, text)
    text = _JSON_SECRET_PATTERN.sub(_redact_json_secret_match, text)
    return text[:limit]


def _redact_quoted_secret_match(match: re.Match[str]) -> str:
    quote = match.group(2)
    return f"{match.group(1)}{quote}[redacted]{quote}"


def _redact_json_secret_match(match: re.Match[str]) -> str:
    return f"{match.group(1)}[redacted]{match.group(3)}"


def _redact_match(match: re.Match[str]) -> str:
    if match.re is _SECRET_PATTERNS[0]:
        return f"{match.group(1)}[redacted]@"
    if match.lastindex == 3:
        return f"{match.group(1)}{match.group(2)}[redacted]"
    if match.lastindex == 2:
        return f"{match.group(1)}[redacted]"
    return "[redacted]"


def redact_data(value: object, string_limit: int = 1000) -> object:
    if isinstance(value, dict):
        redacted: dict[str, object] = {}
        for key, item in value.items():
            key_text = str(key)
            if _SECRET_KEY_PATTERN.search(key_text):
                redacted[key_text] = "[redacted]"
            else:
                redacted[key_text] = redact_data(item, string_limit=string_limit)
        return redacted
    if isinstance(value, list):
        return [redact_data(item, string_limit=string_limit) for item in value]
    if isinstance(value, tuple):
        return [redact_data(item, string_limit=string_limit) for item in value]
    if isinstance(value, str):
        return redact_text(value, limit=string_limit)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(value, limit=string_limit)


def public_exception_message(prefix: str, exc: Exception, limit: int = 300) -> str:
    detail = redact_text(exc, limit=limit)
    if detail:
        return f"{prefix}: {type(exc).__name__}: {detail}"
    return f"{prefix}: {type(exc).__name__}"
