import pytest

from core.scrapers.intervals import normalize_interval, oncalendar_for
from core.scrapers.url import canonicalize_url, normalize_domain, parse_url, parsed_matches_domains
from core.settings.normalizers import normalize_bool, normalize_retention_days


@pytest.mark.parametrize("raw, expected", [
    ("60 minutes", "1h"), ("half-hourly", "30m"), ("1 day", "24h"), ("3h", None),
])
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
    assert canonicalize_url("  HTTPS://www.Example.com/p?q=1#frag  ") == "https://www.Example.com/p?q=1"
    assert parsed_matches_domains(parse_url("https://sub.example.com/p"), ("example.com",))
    assert not parsed_matches_domains(parse_url("https://evil-example.com/p"), ("example.com",))
    for value in ("ftp://example.com/x", "https://u:p@example.com/x", "relative/x"):
        with pytest.raises(ValueError):
            parse_url(value)
