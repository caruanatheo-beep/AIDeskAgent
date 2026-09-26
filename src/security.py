from __future__ import annotations

import html
import ipaddress
import re
from urllib.parse import urlparse

ALLOWED_HOSTS = {
    "fred.stlouisfed.org",
    "www.federalreserve.gov",
    "federalreserve.gov",
    "www.ecb.europa.eu",
    "ecb.europa.eu",
    "www.bls.gov",
    "bls.gov",
    "www.bea.gov",
    "bea.gov",
    "www.bankofengland.co.uk",
    "bankofengland.co.uk",
    "www.boj.or.jp",
    "boj.or.jp",
    "www.oecd.org",
    "oecd.org",
    "www.imf.org",
    "imf.org",
    "www.bis.org",
    "bis.org",
    "ec.europa.eu",
    "eurostat.ec.europa.eu",
    "home.treasury.gov",
    "www.worldbank.org",
    "worldbank.org",
}

# Market snapshots may use a public quote page as a best-effort intraday layer.
# This does not expand the domains available to the agent's web-search tool.
MARKET_DATA_HOSTS = ALLOWED_HOSTS | {"finance.yahoo.com", "api.db.nomics.world"}

MAX_QUERY_LENGTH = 600
INJECTION_MARKERS = (
    "ignore previous",
    "ignore all instructions",
    "system prompt",
    "developer message",
    "execute code",
    "run shell",
    "reveal api key",
    "forget your instructions",
    "act as system",
    "print the secret",
    "exfiltrate",
)


class InputValidationError(ValueError):
    pass


def validate_user_query(query: str) -> str:
    if not isinstance(query, str):
        raise InputValidationError("The question must be text.")
    cleaned = re.sub(r"\s+", " ", query).strip()
    if not cleaned:
        raise InputValidationError("Enter a market question first.")
    if len(cleaned) > MAX_QUERY_LENGTH:
        raise InputValidationError(f"Question is limited to {MAX_QUERY_LENGTH} characters.")
    if any(marker in cleaned.lower() for marker in INJECTION_MARKERS):
        raise InputValidationError("This request contains unsupported instruction-like content.")
    return cleaned


def safe_url(url: str, allowed_hosts: set[str] | None = None) -> str | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    hostname = (parsed.hostname or "").lower().rstrip(".")
    hosts = allowed_hosts or ALLOWED_HOSTS
    try:
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.username or parsed.password or port not in (None, 443):
        return None
    try:
        address = ipaddress.ip_address(hostname)
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            return None
    except ValueError:
        pass
    if hostname not in hosts:
        return None
    return url


def safe_text(value: str, limit: int = 500) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = re.sub(r"\s+", " ", value).strip()[:limit]
    return html.escape(value, quote=True)


def untrusted_text(value: str, limit: int = 4000) -> str:
    """Normalize external text and neutralize common instruction-injection phrases."""
    cleaned = re.sub(r"<script\b[^>]*>.*?</script>", " ", value or "", flags=re.I | re.S)
    cleaned = re.sub(r"<style\b[^>]*>.*?</style>", " ", cleaned, flags=re.I | re.S)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    for marker in INJECTION_MARKERS:
        cleaned = re.sub(re.escape(marker), "[instruction-like text removed]", cleaned, flags=re.I)
    return redact_secrets(cleaned[:limit])


def redact_secrets(text: str) -> str:
    patterns = [
        r"(?i)(api[_-]?key\s*[:=]\s*)\S+",
        r"(?i)(token\s*[:=]\s*)\S+",
        r"hf_[A-Za-z0-9]{20,}",
        r"sk-[A-Za-z0-9_-]{16,}",
    ]
    for pattern in patterns:
        text = re.sub(pattern, "[REDACTED]", text)
    return text
