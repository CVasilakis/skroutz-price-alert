"""Host declaration and absolute product URL boundary tests."""

import unittest

from core.scrapers.base.url import (
    is_absolute_http_url,
    normalize_domain,
    url_host,
    url_matches_domains,
)


class TestDomainNormalization(unittest.TestCase):
    def test_dns_idna_case_trailing_dot_and_ips(self):
        self.assertEqual(normalize_domain(" Example.COM. "), "example.com")
        self.assertEqual(normalize_domain("BÜCHER.example"), "xn--bcher-kva.example")
        self.assertEqual(normalize_domain("127.0.0.1"), "127.0.0.1")
        self.assertEqual(normalize_domain("2001:0db8::1"), "2001:db8::1")

    def test_rejects_non_host_declarations(self):
        for value in (
            "https://example.com", "example.com/path", "user@example.com",
            "example.com:443", "[2001:db8::1]", "", "bad label.example",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_domain(value)


class TestProductUrls(unittest.TestCase):
    def test_absolute_http_urls_and_explicit_ports(self):
        for value in (
            "http://example.com/p/1", "https://EXAMPLE.com.:8443/p/1",
            "http://127.0.0.1:8123/p", "https://[2001:db8::1]:443/p",
            "https://bücher.example/p",
        ):
            with self.subTest(value=value):
                self.assertTrue(is_absolute_http_url(value))

        self.assertEqual(url_host("https://EXAMPLE.com.:8443/p"), "example.com")
        self.assertTrue(url_matches_domains("https://shop.example.com:9/p", ["example.com"]))

    def test_rejects_relative_unsupported_credentialed_and_malformed_urls(self):
        for value in (
            "//example.com/p", "/p", "ftp://example.com/p",
            "https://user:pass@example.com/p", "https://example.com:bad/p",
            "https://example.com:99999/p", "https://[broken/p", "example.com/p",
        ):
            with self.subTest(value=value):
                self.assertFalse(is_absolute_http_url(value))


if __name__ == "__main__":
    unittest.main()
