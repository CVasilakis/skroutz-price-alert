"""Strict URL and domain validation shared across the scraper domain."""

import ipaddress
from collections.abc import Iterable
from urllib.parse import SplitResult, urlsplit, urlunsplit


def normalize_domain(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("domain must be a nonblank host")
    candidate = value.strip().removesuffix(".")
    if not candidate or any(char in candidate for char in "/?#@") or "://" in candidate:
        raise ValueError("domain must contain a host only")
    if candidate.startswith("[") or candidate.endswith("]"):
        raise ValueError("IPv6 domains must be declared without brackets")
    try:
        return ipaddress.ip_address(candidate).compressed.casefold()
    except ValueError:
        if candidate.replace(".", "").isdigit() or ":" in candidate:
            raise ValueError("domain is not a valid host") from None
    try:
        normalized = candidate.encode("idna").decode("ascii").casefold()
    except UnicodeError as exc:
        raise ValueError("domain is not a valid DNS name") from exc
    labels = normalized.split(".")
    if len(normalized) > 253 or any(
        not label
        or len(label) > 63
        or label[0] == "-"
        or label[-1] == "-"
        or not all(char.isalnum() or char == "-" for char in label)
        for label in labels
    ):
        raise ValueError("domain is not a valid DNS name")
    return normalized


def parse_url(value: object) -> SplitResult:
    """Validate and parse one absolute, credential-free HTTP(S) URL."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("URL must be a nonblank string")
    raw = value.strip()
    try:
        parsed = urlsplit(raw)
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
            raise ValueError("URL must be absolute HTTP(S)")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("URL must not contain credentials")
        _ = parsed.port
        if parsed.hostname is None:
            raise ValueError("URL must contain a host")
        normalize_domain(parsed.hostname)
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f"invalid URL: {exc}") from exc
    return parsed


def _canonical_netloc(parsed: SplitResult) -> str:
    """Rebuild the authority from its normalized host, preserving any explicit port.

    Hosts are case-insensitive, so the raw authority would otherwise let one address
    take several canonical forms and, through them, several persisted identities. The
    host is normalized by the same function domain matching uses, which keeps the form
    a URL is stored under identical to the form it is matched against.
    """
    host = normalize_domain(parsed.hostname)
    if ":" in host:  # urlsplit strips the brackets IPv6 literals need in an authority
        host = f"[{host}]"
    return host if parsed.port is None else f"{host}:{parsed.port}"


def canonicalize_url(value: object) -> str:
    """Trim, normalize the authority, and remove only the fragment.

    Queries remain semantically significant, and an explicit port is preserved: a URL
    only ever loses the parts that cannot change which resource it addresses.
    """
    parsed = parse_url(value)
    return urlunsplit(
        (
            parsed.scheme.casefold(),
            _canonical_netloc(parsed),
            parsed.path,
            parsed.query,
            "",
        )
    )


def host_matches_domain(host: str, domain: str) -> bool:
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        return host == domain or host.endswith("." + domain)
    return host == domain


def parsed_matches_domains(parsed: SplitResult, domains: Iterable[str]) -> bool:
    host = normalize_domain(parsed.hostname)
    return any(host_matches_domain(host, domain) for domain in domains)
