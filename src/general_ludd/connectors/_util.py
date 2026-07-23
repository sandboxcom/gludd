from __future__ import annotations

from datetime import UTC, datetime

from general_ludd.security.ssrf import is_url_blocked


def validate_base_url(base_url: str) -> str:
    if is_url_blocked(base_url, scheme_allowlist=("http", "https")):
        raise ValueError(f"base_url host is blocked (loopback/private/metadata): {base_url!r}")
    return base_url.rstrip("/")


def parse_timestamp(value: object) -> float | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%b %d %Y %H:%M:%S", "%B %d %Y %H:%M:%S"):
            try:
                dt = datetime.strptime(text, fmt).replace(tzinfo=UTC)
                break
            except ValueError:
                continue
        else:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()
