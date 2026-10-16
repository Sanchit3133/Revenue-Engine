import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.ai_client import generate_landing_page_draft


class TestAIDeliverableDraft(unittest.TestCase):
    def test_missing_huggingface_token_uses_safe_template(self):
        with patch("app.ai_client.settings", SimpleNamespace(hf_token=None, hf_api_key=None)):
            filename, content, source, diagnostic = generate_landing_page_draft({
                "business_name": "Example <script>alert(1)</script>",
                "service": "IELTS coaching",
                "audience": "adult learners",
                "requirements": "Show course outcomes without guaranteeing scores.",
            })
        self.assertEqual(filename, "landing-page-draft.html")
        self.assertEqual(source, "template_fallback")
        self.assertIn("HF_TOKEN is not configured", diagnostic)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", content)
        self.assertNotIn("<script>alert(1)</script>", content)
        self.assertIn('name="robots" content="noindex,nofollow"', content)
        self.assertIn("Draft for owner review", content)

    def test_valid_huggingface_json_is_rendered_inside_fixed_template(self):
        model_content = {
            "title": "Example Coaching",
            "headline": "Prepare with a clear plan",
            "subheadline": "A course designed for adult learners.",
            "benefits": ["Practice", "Feedback", "Structure"],
            "sections": [{"heading": "Course approach", "body": "Practice and feedback."}],
            "cta_text": "Ask about the course",
            "seo_description": "Learn about the course.",
        }
        response = Mock(status_code=200)
        response.json.return_value = {"choices": [{"message": {"content": json.dumps(model_content)}}]}
        fake_settings = SimpleNamespace(hf_token="test-token", hf_api_key=None, hf_api_url="https://hf.invalid/chat/completions", hf_model="test/model")
        with patch("app.ai_client.settings", fake_settings), patch("app.ai_client.requests.post", return_value=response) as post:
            _filename, html, source, diagnostic = generate_landing_page_draft({"business_name": "Example Coaching", "service": "IELTS coaching"})
        self.assertEqual(source, "huggingface")
        self.assertIsNone(diagnostic)
        self.assertIn("Prepare with a clear plan", html)
        self.assertNotIn("<script", html.lower())
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer test-token")

    def test_invalid_model_json_falls_back_and_does_not_render_raw_model_html(self):
        response = Mock(status_code=200)
        response.json.return_value = {"choices": [{"message": {"content": "<script>alert(1)</script>"}}]}
        fake_settings = SimpleNamespace(hf_token="test-token", hf_api_key=None, hf_api_url="https://hf.invalid/chat/completions", hf_model="test/model")
        with patch("app.ai_client.settings", fake_settings), patch("app.ai_client.requests.post", return_value=response):
            _filename, html, source, diagnostic = generate_landing_page_draft({"business_name": "Safe business"})
        self.assertEqual(source, "template_fallback")
        self.assertTrue(diagnostic)
        self.assertNotIn("<script>alert(1)</script>", html)


if __name__ == "__main__":
    unittest.main()
