import io
import random
import unittest
import zipfile

from scripts.overture_source import City, parse_cities_zip
from scripts.overture_discovery import (
    SEGMENTS, _emails_from_html, choose_random_segment,
    normalize_feature,
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
                "sources": [{"dataset": "ExampleOpenData"}],
            }
        }
        record = normalize_feature(feature)
        self.assertIsNotNone(record)
        self.assertNotIn("email", record)
        self.assertIn("place_id", record)
        self.assertEqual(record["source_names"], ["ExampleOpenData"])

    def test_email_extraction_uses_published_addresses_only(self):
        addresses = _emails_from_html(
            '<p>Contact us at admissions@school.example</p>'
            '<a href="mailto:info@school.example">Email</a>'
            '<p>Fake placeholder@example.com</p>'
        )
        self.assertIn("admissions@school.example", addresses)
        self.assertIn("info@school.example", addresses)
        self.assertNotIn("placeholder@example.com", addresses)

    def test_random_segment_comes_from_configured_market_profiles(self):
        segment, query, keywords = choose_random_segment(rng=random.Random(7))
        self.assertIn(segment, {item[0] for item in SEGMENTS})
        self.assertTrue(query)
        self.assertTrue(keywords)


if __name__ == "__main__":
    unittest.main()
