import pytest

from core.scrapers.domain import (
    canonicalize_url,
    normalize_domain,
    parse_url,
    parsed_matches_domains,
)
from core.scrapers.framework.intervals import normalize_interval, oncalendar_for
from core.settings import normalize_bool, normalize_retention_days


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("60 minutes", "1h"),
        ("half-hourly", "30m"),
        ("1 day", "24h"),
        ("3h", None),
    ],
)
def test_interval_aliases(raw, expected):
    assert normalize_interval(raw) == expected


def test_setting_normalizers_are_strict():
    assert normalize_bool(True) is True
    assert normalize_bool("false") is False
    assert normalize_bool("maybe") is None
    assert normalize_retention_days("14 days") == 14
    assert normalize_retention_days(True) is None
    assert normalize_retention_days(31) is None
    assert oncalendar_for("1h") == "hourly"


@pytest.mark.parametrize("domain", ["https://example.com", "x/y", "bad:80", "-bad.test"])
def test_invalid_domains(domain):
    with pytest.raises(ValueError):
        normalize_domain(domain)


def test_url_security_domain_matching_and_canonicalization():
    assert (
        canonicalize_url("  HTTPS://www.Example.com/p?q=1#frag  ")
        == "https://www.example.com/p?q=1"
    )
    assert parsed_matches_domains(parse_url("https://sub.example.com/p"), ("example.com",))
    assert not parsed_matches_domains(parse_url("https://evil-example.com/p"), ("example.com",))
    for value in ("ftp://example.com/x", "https://u:p@example.com/x", "relative/x"):
        with pytest.raises(ValueError):
            parse_url(value)


@pytest.mark.parametrize(
    "raw, expected",
    [
        # One host, one canonical form: case, a root dot, a Unicode spelling, and an
        # uncompressed IPv6 literal must not become separate persisted identities.
        ("https://WWW.Example.COM/ad/1", "https://www.example.com/ad/1"),
        ("https://example.com./ad/1", "https://example.com/ad/1"),
        ("https://παράδειγμα.gr/ad", "https://xn--hxajbheg2az3al.gr/ad"),
        ("http://[2001:0DB8:0000::1]/ad", "http://[2001:db8::1]/ad"),
        # Parts that can change which resource is addressed survive untouched.
        ("https://Example.com:8443/Ad/ONE?Q=A", "https://example.com:8443/Ad/ONE?Q=A"),
    ],
)
def test_canonical_url_authority_is_host_normalized(raw, expected):
    assert canonicalize_url(raw) == expected
    assert canonicalize_url(expected) == expected  # canonical form is a fixed point


def test_canonical_host_matches_the_form_domain_matching_uses():
    # State stores canonical URLs and rejects any that are not already canonical, so
    # the stored host form has to be the one parsed_matches_domains compares against.
    parsed = parse_url(canonicalize_url("https://WWW.Example.COM/ad/1"))
    assert parsed.netloc == normalize_domain("WWW.Example.COM")
    assert parsed_matches_domains(parsed, ("example.com",))
