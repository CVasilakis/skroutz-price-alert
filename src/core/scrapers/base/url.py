"""Import-light URL and host validation shared by scraper boundaries."""

import ipaddress
from collections.abc import Iterable
from urllib.parse import urlsplit


def normalize_domain(value: object) -> str:
    """Return a canonical host-only domain/IP, raising ``ValueError`` if invalid."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("domain must be a nonblank host")
    value = value.strip()
    if any(char in value for char in "/?#@") or "://" in value:
        raise ValueError("domain must contain a host only (no scheme, path, or credentials)")

    candidate = value[:-1] if value.endswith(".") else value
    if not candidate:
        raise ValueError("domain must not be blank")

    # Brackets are URL notation, not part of a host declaration. Accept bare IPv6.
    if candidate.startswith("[") or candidate.endswith("]"):
        raise ValueError("IPv6 domains must be declared without brackets")
    try:
        return ipaddress.ip_address(candidate).compressed.casefold()
    except ValueError:
        if candidate.replace(".", "").isdigit():
            raise ValueError("domain is not a valid IP address")

    if ":" in candidate:
        raise ValueError("domain must not declare a port")
    try:
        normalized = candidate.encode("idna").decode("ascii").casefold()
    except (UnicodeError, ValueError) as exc:
        raise ValueError("domain is not a valid DNS name") from exc
    if len(normalized) > 253 or any(
        not label or len(label) > 63 or label[0] == "-" or label[-1] == "-"
        or not all(char.isalnum() or char == "-" for char in label)
        for label in normalized.split(".")
    ):
        raise ValueError("domain is not a valid DNS name")
    return normalized


def url_host(value: object) -> str | None:
    """Return the normalized host of an absolute HTTP(S) URL, else ``None``."""
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        # Accessing port deliberately validates malformed/out-of-range port syntax.
        _ = parsed.port
        hostname = parsed.hostname
        if hostname is None:
            return None
        return normalize_domain(hostname)
    except (ValueError, UnicodeError):
        return None


def is_absolute_http_url(value: object) -> bool:
    """Whether *value* is an absolute credential-free HTTP(S) URL."""
    return url_host(value) is not None


def clean_url(value: object) -> str:
    """Return an absolute URL without query parameters or a fragment.

    Invalid or non-string values return an empty string. This is the canonical URL
    representation used by item identity and JSON cleanup.
    """
    if not isinstance(value, str) or not value:
        return ""
    try:
        parts = urlsplit(value)
    except ValueError:
        return ""
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def host_matches_domain(host: str, domain: str) -> bool:
    """Match a host to a domain on a DNS label boundary (IPs match exactly)."""
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        return host == domain or host.endswith("." + domain)
    return host == domain


def url_matches_domains(value: object, domains: Iterable[object]) -> bool:
    """Whether an absolute HTTP(S) URL belongs to one of the supplied domains."""
    host = url_host(value)
    if host is None:
        return False
    try:
        return any(host_matches_domain(host, normalize_domain(domain)) for domain in domains)
    except (TypeError, ValueError):
        return False
