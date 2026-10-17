import os
import unittest
from unittest.mock import patch
from app.config import load_settings, DEFAULT_LANDING_PAGE_URL


class TestConfiguration(unittest.TestCase):
    def test_landing_page_default_is_current_site_services_section(self):
        with patch.dict(os.environ, {"LANDING_PAGE_URL": "", "EMAIL_PROVIDER": "brevo"}, clear=False):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("LANDING_PAGE_URL", None)
                settings = load_settings()
        self.assertEqual(settings.landing_page_url, DEFAULT_LANDING_PAGE_URL)

    def test_unknown_email_provider_is_rejected(self):
        with patch.dict(os.environ, {"EMAIL_PROVIDER": "unknown-mailer"}, clear=False):
            with self.assertRaisesRegex(ValueError, "EMAIL_PROVIDER"):
                load_settings()

    def test_invalid_delay_range_is_rejected(self):
        with patch.dict(os.environ, {"MIN_SEND_DELAY": "10", "MAX_SEND_DELAY": "5"}, clear=False):
            with self.assertRaisesRegex(ValueError, "MAX_SEND_DELAY"):
                load_settings()

    def test_published_environment_requires_postgresql_url(self):
        with patch.dict(
            os.environ,
            {
                "ENVIRONMENT": "development",
                "REPL_DEPLOYMENT": "1",
                "DATABASE_URL": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "DATABASE_URL"):
                load_settings()


if __name__ == "__main__":
    unittest.main()
