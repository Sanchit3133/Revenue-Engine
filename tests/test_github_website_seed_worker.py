import unittest

from scripts.github_website_seed_worker import (
    extract_business_name,
    extract_public_email,
    parse_website_urls,
)


class TestWebsiteSeedWorkerParsing(unittest.TestCase):
    def test_parses_up_to_five_https_sites(self):
        result = parse_website_urls(
            "https://alpha-academy.in, https://www.beta-training.org; https://gamma-school.edu"
        )
        self.assertEqual(len(result), 3)
        self.assertTrue(all(url.startswith("https://") for url in result))

    def test_deduplicates_www_alias(self):
        result = parse_website_urls("https://academy.example.in https://www.academy.example.in")
        self.assertEqual(len(result), 1)

    def test_rejects_http_seed(self):
        with self.assertRaises(ValueError):
            parse_website_urls("http://academy.example.in")

    def test_rejects_local_hostname(self):
        with self.assertRaises(ValueError):
            parse_website_urls("https://localhost")

    def test_rejects_ip_literal(self):
        with self.assertRaises(ValueError):
            parse_website_urls("https://8.8.8.8")

    def test_rejects_more_than_five_sites(self):
        sites = " ".join(f"https://academy{i}.example.in" for i in range(6))
        with self.assertRaises(ValueError):
            parse_website_urls(sites)

    def test_extracts_name_from_site_metadata(self):
        html = """
        <html><head><meta property="og:site_name" content="North Star Coaching"></head>
        <body><h1>Welcome</h1></body></html>
        """
        self.assertEqual(extract_business_name(html), "North Star Coaching")

    def test_extracts_public_mailto_email(self):
        html = """
        <html><body><a href="mailto:admissions@northstarcoaching.in">Admissions</a></body></html>
        """
        self.assertEqual(
            extract_public_email(html),
            "admissions@northstarcoaching.in",
        )

    def test_ignores_no_reply_email(self):
        html = "<html><body>no-reply@northstarcoaching.in</body></html>"
        self.assertEqual(extract_public_email(html), "")


if __name__ == "__main__":
    unittest.main()
