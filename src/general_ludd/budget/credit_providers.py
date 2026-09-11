"""Provider endpoints and response parsers for prepaid credit tracking."""

from __future__ import annotations

from typing import Any

SUPPORTED_SERVICES: tuple[str, ...] = ("deepseek", "openai", "zai", "openrouter")

SERVICE_CONFIG: dict[str, dict[str, str]] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "balance_path": "/user/balance",
        "auth_env_var": "DEEPSEEK_API_KEY",
    },
    "openai": {
        "base_url": "https://api.openai.com",
        "balance_path": "/v1/organization/costs",
        "auth_env_var": "OPENAI_API_KEY",
    },
    "zai": {
        "base_url": "https://api.z.ai/api",
        "balance_path": "/paas/v4/usage",
        "auth_env_var": "ZAI_API_KEY",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai",
        "balance_path": "/api/credits",
        "auth_env_var": "OPENROUTER_API_KEY",
    },
}


class Unparseable(ValueError):
    """Raised when a provider returns an unsupported JSON shape."""


def parse_deepseek(data: Any) -> tuple[float, str]:
    """Return the USD wallet, or the first available DeepSeek wallet."""
    wallets = (data or {}).get("wallets") or []
    for wallet in wallets:
        if str(wallet.get("currency", "")).upper() == "USD":
            return float(wallet["balance"]), "USD"
    if wallets:
        wallet = wallets[0]
        return float(wallet["balance"]), str(wallet.get("currency", "USD")).upper()
    raise Unparseable("no wallets in DeepSeek response")


def parse_openai(data: Any) -> tuple[float, str]:
    """Sum OpenAI cost line items because its API exposes usage, not balance."""
    items = (data or {}).get("data") or []
    total = 0.0
    for item in items:
        line = item.get("line_item")
        if line is not None:
            total += float(line)
    return total, "USD"


def parse_zai(data: Any) -> tuple[float, str]:
    """Read the Z.AI balance and normalized currency."""
    inner = (data or {}).get("data") or {}
    if "balance" not in inner:
        raise Unparseable("no balance field in Z.AI response")
    return float(inner["balance"]), str(inner.get("currency", "USD")).upper()


def parse_openrouter(data: Any) -> tuple[float, str]:
    """Calculate remaining OpenRouter credit from credits minus usage."""
    inner = (data or {}).get("data") or {}
    if "total_credits" not in inner or "total_usage" not in inner:
        raise Unparseable("missing total_credits/total_usage in OpenRouter response")
    return float(inner["total_credits"]) - float(inner["total_usage"]), "USD"


PARSERS: dict[str, Any] = {
    "deepseek": parse_deepseek,
    "openai": parse_openai,
    "zai": parse_zai,
    "openrouter": parse_openrouter,
}


__all__ = [
    "PARSERS",
    "SERVICE_CONFIG",
    "SUPPORTED_SERVICES",
    "Unparseable",
    "parse_deepseek",
    "parse_openai",
    "parse_openrouter",
    "parse_zai",
]
