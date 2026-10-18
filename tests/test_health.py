import unittest

from app.web import create_app


class TestHealthEndpoint(unittest.TestCase):
    def test_healthz_returns_ok_without_database_details(self):
        app = create_app({"TESTING": True, "SECRET_KEY": "health-test-secret"})
        response = app.test_client().get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "ok"})
        self.assertNotIn(b"DATABASE_URL", response.data)
        self.assertNotIn(b"neondb", response.data)


if __name__ == "__main__":
    unittest.main()
