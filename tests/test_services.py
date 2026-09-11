import unittest

from app.services import (
    SERVICE_CATALOG,
    get_service,
    list_services,
    service_keys,
    services_for_category,
)


class TestServiceCatalogue(unittest.TestCase):

    def test_catalogue_is_not_empty(self):
        self.assertTrue(SERVICE_CATALOG)

    def test_expected_services_exist(self):
        expected_services = {
            "LANDING_PAGE",
            "PAYMENT_PAGE",
            "LEAD_CAPTURE",
            "AUTO_RESPONSE",
            "FOLLOW_UP_SYSTEM",
            "BUSINESS_AUTOMATION",
            "CUSTOM_OUTREACH_SYSTEM",
            "CUSTOM_AUTOMATION",
        }

        self.assertTrue(
            expected_services.issubset(set(service_keys()))
        )

    def test_landing_page_price_is_490(self):
        service = get_service("LANDING_PAGE")

        self.assertIsNotNone(service)
        self.assertEqual(service.starting_price, 490)

    def test_landing_page_is_website_service(self):
        service = get_service("LANDING_PAGE")

        self.assertEqual(service.category, "website")

    def test_all_services_have_required_information(self):
        for service in list_services():
            self.assertTrue(service.key)
            self.assertTrue(service.name)
            self.assertTrue(service.category)

            self.assertIsInstance(
                service.problems_solved,
                list,
            )

            self.assertIsInstance(
                service.business_signals,
                list,
            )

            self.assertIsInstance(
                service.deliverables,
                list,
            )

            self.assertGreater(
                len(service.problems_solved),
                0,
            )

            self.assertGreater(
                len(service.business_signals),
                0,
            )

            self.assertGreater(
                len(service.deliverables),
                0,
            )

    def test_all_starting_prices_are_valid(self):
        for service in list_services():
            self.assertIsNotNone(service.starting_price)
            self.assertIsInstance(
                service.starting_price,
                int,
            )

            self.assertGreater(
                service.starting_price,
                0,
            )

    def test_unknown_service_returns_none(self):
        self.assertIsNone(
            get_service("THIS_DOES_NOT_EXIST")
        )

    def test_services_for_automation_category(self):
        automation_services = services_for_category(
            "automation"
        )

        keys = {
            service.key
            for service in automation_services
        }

        self.assertIn(
            "AUTO_RESPONSE",
            keys,
        )

        self.assertIn(
            "FOLLOW_UP_SYSTEM",
            keys,
        )

        self.assertIn(
            "BUSINESS_AUTOMATION",
            keys,
        )

    def test_custom_services_are_in_custom_category(self):
        custom_services = services_for_category(
            "custom"
        )

        keys = {
            service.key
            for service in custom_services
        }

        self.assertIn(
            "CUSTOM_OUTREACH_SYSTEM",
            keys,
        )

        self.assertIn(
            "CUSTOM_AUTOMATION",
            keys,
        )


if __name__ == "__main__":
    unittest.main()