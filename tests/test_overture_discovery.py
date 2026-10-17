import io
import random
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

from scripts.overture_source import City, parse_cities_zip
from scripts.overture_discovery import (
    SEGMENTS, _emails_from_html, _fetch_html, _official_https_site, choose_random_segment,
    _weighted_segment_order, discover_and_persist, normalize_feature,
)


def geonames_payload(rows):
    text = io.StringIO()
    import csv
    writer = csv.writer(text, delimiter="\t", lineterminator="\n")
    for row in rows:
        writer.writerow(row)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("cities15000.txt", text.getvalue())
    return output.getvalue()


class TestOvertureSource(unittest.TestCase):
    def test_random_city_pool_filters_country_and_population(self):
        def row(name, cc, population, lat, lon):
            parts = ["0"] * 19
            parts[1] = name
            parts[4] = str(lat)
            parts[5] = str(lon)
            parts[6] = "P"
            parts[8] = cc
            parts[14] = str(population)
            return parts
        payload = geonames_payload([
            row("Nagpur", "IN", 2400000, 21.15, 79.09),
            row("Small Village", "IN", 12000, 21.2, 79.1),
            row("Kampala", "UG", 1600000, 0.31, 32.58),
        ])
        cities = parse_cities_zip(payload, "IN", 40000)
        self.assertEqual([city.name for city in cities], ["Nagpur"])
        self.assertEqual(cities[0].country_code, "IN")

    def test_rejects_non_https_or_low_confidence_features(self):
        base = {
            "id": "place-1",
            "names": {"primary": "Example Coaching Institute"},
            "websites": ["https://example.in/"],
            "confidence": 0.9,
            "taxonomy": {"primary": "education"},
            "sources": [{"dataset": "meta", "license": "CDLA-Permissive-2.0"}],
        }
        self.assertIsNotNone(normalize_feature({"properties": base}))
        self.assertIsNone(normalize_feature({"properties": {**base, "websites": ["http://example.in/"]}}))
        self.assertIsNone(normalize_feature({"properties": {**base, "confidence": 0.2}}))

    def test_normalized_feature_does_not_use_upstream_email_fields(self):
        feature = {
            "properties": {
                "id": "place-2",
                "names": {"primary": "Training Academy"},
                "websites": ["https://academy.example/"],
                "confidence": 0.9,
                "taxonomy": {"primary": "education"},
                "emails": ["somebody@not-the-official-site.example"],
                "sources": [{"dataset": "meta", "license": "CDLA-Permissive-2.0"}],
            }
        }
        record = normalize_feature(feature)
        self.assertIsNotNone(record)
        self.assertNotIn("email", record)
        self.assertIn("place_id", record)
        self.assertEqual(record["source_names"], ["meta"])
        self.assertEqual(record["source_licenses"], [("meta", "CDLA-Permissive-2.0")])

    def test_email_extraction_uses_published_addresses_only(self):
        addresses = _emails_from_html(
            '<p>Contact us at admissions@school.example</p>'
            '<a href="mailto:info@school.example">Email</a>'
            '<p>Fake placeholder@example.com</p>'
        )
        self.assertIn("admissions@school.example", addresses)
        self.assertIn("info@school.example", addresses)
        self.assertNotIn("placeholder@example.com", addresses)

    def test_rejects_missing_or_foursquare_source_license(self):
        base = {
            "id": "place-license",
            "names": {"primary": "Example Coaching Institute"},
            "websites": ["https://example.in/"],
            "confidence": 0.9,
            "taxonomy": {"primary": "education"},
        }
        self.assertIsNone(normalize_feature({"properties": {**base, "sources": [{"dataset": "unknown"}]}}))
        self.assertIsNone(normalize_feature({"properties": {**base, "sources": [{"dataset": "Foursquare", "license": "Apache-2.0"}]}}))

    def test_known_overture_source_license_is_resolved_when_record_license_is_null(self):
        feature = {
            "properties": {
                "id": "place-license-map",
                "names": {"primary": "Test Academy"},
                "websites": ["https://academy.example/"],
                "confidence": 0.9,
                "taxonomy": {"primary": "education"},
                "sources": [{"dataset": "meta", "license": None}],
            }
        }
        record = normalize_feature(feature)
        self.assertIsNotNone(record)
        self.assertEqual(record["source_licenses"], [("meta", "CDLA-Permissive-2.0")])

    def test_official_site_crawler_rejects_ip_literal_and_private_hosts(self):
        self.assertEqual(_official_https_site("http://example.in"), "")
        self.assertEqual(_official_https_site("https://127.0.0.1"), "")
        self.assertEqual(_official_https_site("https://[::1]"), "")
        self.assertEqual(_official_https_site("https://localhost"), "")
        self.assertEqual(_official_https_site("https://service.local"), "")
        self.assertEqual(_official_https_site("https://example.in:8443"), "")
        self.assertEqual(_official_https_site("https://www.example.in/course"), "https://www.example.in")

    def test_external_redirect_is_blocked_before_second_request(self):
        class Response:
            def __init__(self, status, headers=None, body=b""):
                self.status_code = status
                self.headers = headers or {}
                self.encoding = "utf-8"
                self.body = body
                self.closed = False
            def iter_content(self, chunk_size):
                yield self.body
            def close(self):
                self.closed = True

        redirect = Response(302, {"Location": "https://external.example/contact"})
        with patch("scripts.overture_discovery.requests.get", return_value=redirect) as mocked:
            content = _fetch_html(
                "https://official.example",
                "https://official.example/contact",
            )
        self.assertEqual(content, "")
        self.assertEqual(mocked.call_count, 1)
        self.assertFalse(mocked.call_args.kwargs.get("allow_redirects", True))
        self.assertTrue(redirect.closed)

    def test_same_host_redirect_is_followed(self):
        class Response:
            def __init__(self, status, headers=None, body=b""):
                self.status_code = status
                self.headers = headers or {}
                self.encoding = "utf-8"
                self.body = body
                self.closed = False
            def iter_content(self, chunk_size):
                yield self.body
            def close(self):
                self.closed = True

        redirect = Response(301, {"Location": "/contact-us"})
        final = Response(200, {"content-type": "text/html"}, b"<p>Hello</p>")
        with patch("scripts.overture_discovery.requests.get", side_effect=[redirect, final]) as mocked:
            content = _fetch_html(
                "https://official.example",
                "https://official.example/contact",
            )
        self.assertEqual(content, "<p>Hello</p>")
        self.assertEqual(mocked.call_count, 2)
        self.assertTrue(redirect.closed)
        self.assertTrue(final.closed)

    def test_random_segment_comes_from_configured_market_profiles(self):
        segment, query, keywords = choose_random_segment(rng=random.Random(7))
        self.assertIn(segment, {item[0] for item in SEGMENTS})
        self.assertTrue(query)
        self.assertTrue(keywords)


class TestMultiCityDiscovery(unittest.TestCase):
    def test_tries_another_random_city_and_matches_all_education_segments(self):
        cities = [
            City("Khopoli", "IN", 18.79, 73.34, 100000),
            City("Nagpur", "IN", 21.15, 79.09, 2000000),
        ]
        candidate = {
            "properties": {
                "id": "academy-001",
                "names": {"primary": "Bright IELTS Coaching Academy"},
                "websites": ["https://academy.in/"],
                "confidence": 0.95,
                "taxonomy": {"primary": "language training education"},
                "sources": [{"dataset": "meta", "license": "CDLA-Permissive-2.0"}],
            }
        }

        class EmptyCursor:
            def fetchall(self):
                return []

        class ReadOnlyFakeConnection:
            def execute(self, _query, _params=()):
                return EmptyCursor()

        class StableRandom:
            def shuffle(self, values):
                return None

            def choices(self, population, weights=None, k=1):
                return [population[0]]

        with (
            patch(
                "scripts.overture_discovery._eligible_cities",
                return_value=(cities, "India"),
            ),
            patch(
                "scripts.overture_discovery.download_city_places",
                side_effect=[[], [candidate]],
            ) as download,
            patch(
                "scripts.overture_discovery.find_official_email",
                return_value=("admissions@academy.in", "public_email_found"),
            ) as email_lookup,
            patch(
                "scripts.overture_discovery.score_business",
                return_value=SimpleNamespace(
                    score=95, qualification="HIGH", reasons=["education training"],
                ),
            ),
        ):
            result = discover_and_persist(
                ReadOnlyFakeConnection(),
                country="India",
                max_new=1,
                rng=StableRandom(),
                persist=False,
            )

        self.assertEqual(download.call_count, 2)
        self.assertEqual(email_lookup.call_count, 1)
        self.assertEqual(result["cities_attempted"], 2)
        self.assertEqual(result["city"], "Khopoli, Nagpur")
        self.assertEqual(result["site_checks"], 1)
        self.assertEqual(result["published_emails_found"], 1)
        self.assertEqual(result["qualified"], 1)
        self.assertEqual(result["saved"], 0)
        self.assertEqual(result["lead_ids"], [])

    def test_weighted_segment_order_contains_each_segment_once(self):
        ordered = _weighted_segment_order(rng=random.Random(7))
        self.assertEqual(len(ordered), len(SEGMENTS))
        self.assertEqual({item[0] for item in ordered}, {item[0] for item in SEGMENTS})


if __name__ == "__main__":
    unittest.main()
