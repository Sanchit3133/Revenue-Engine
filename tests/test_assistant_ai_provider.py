import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.web.consultation_ai import (
    BUSINESS_GROWTH_OPENING,
    build_consultation_reply,
    provider_reply,
    should_ask_business_context,
)


class TestConsultationAIProvider(unittest.TestCase):
    def test_generic_first_message_asks_business_type_and_growth_goal(self):
        with patch("app.web.consultation_ai.requests.post") as post:
            reply, using_ai = build_consultation_reply(
                "how can I increase business", {}, []
            )
        self.assertEqual(reply, BUSINESS_GROWTH_OPENING)
        self.assertIn("what kind of business", reply.lower())
        self.assertIn("growth goal", reply.lower())
        self.assertFalse(using_ai)
        post.assert_not_called()

    def test_opening_is_not_repeated_after_assistant_has_replied(self):
        history = [
            {"role": "user", "content": "how can I increase business"},
            {"role": "assistant", "content": BUSINESS_GROWTH_OPENING},
        ]
        self.assertFalse(
            should_ask_business_context(
                "I run a coaching centre", {}, history
            )
        )

    def test_known_business_type_skips_generic_opening(self):
        self.assertFalse(
            should_ask_business_context(
                "how can I increase business",
                {"business_type": "coaching centre"},
                [],
            )
        )

    def test_groq_uses_configured_key_endpoint_and_model(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "choices": [{"message": {"content": "What is your main growth goal?"}}]
        }
        fake_settings = SimpleNamespace(
            ai_provider="groq",
            groq_api_key="test-groq-key",
            groq_api_url="https://groq.example/chat/completions",
            groq_model="openai/gpt-oss-20b",
            hf_token="must-not-be-used",
        )
        with patch("app.web.consultation_ai.settings", fake_settings), patch(
            "app.web.consultation_ai.requests.post", return_value=response
        ) as post:
            reply = provider_reply(
                "I run a coaching centre",
                {"business_type": "coaching centre"},
                [],
            )

        self.assertEqual(reply, "What is your main growth goal?")
        self.assertEqual(post.call_args.args[0], "https://groq.example/chat/completions")
        self.assertEqual(
            post.call_args.kwargs["headers"]["Authorization"],
            "Bearer test-groq-key",
        )
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "openai/gpt-oss-20b")
        system_prompt = payload["messages"][0]["content"].lower()
        self.assertIn("before the customer has disclosed a budget", system_prompt)
        self.assertIn("never offer discounts", system_prompt)
        self.assertIn("only the owner can approve delivery", system_prompt)
        self.assertIn("do not assume every business has a lead-follow-up problem", system_prompt)

    def test_huggingface_provider_remains_supported(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "choices": [{"message": {"content": "Tell me about your current process."}}]
        }
        fake_settings = SimpleNamespace(
            ai_provider="huggingface",
            hf_token="test-hf-key",
            hf_api_url="https://hf.example/chat/completions",
            hf_model="test/hf-model",
        )
        with patch("app.web.consultation_ai.settings", fake_settings), patch(
            "app.web.consultation_ai.requests.post", return_value=response
        ) as post:
            reply = provider_reply("I run a shop", {"business_type": "retail"}, [])

        self.assertEqual(reply, "Tell me about your current process.")
        self.assertEqual(post.call_args.args[0], "https://hf.example/chat/completions")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "test/hf-model")

    def test_provider_failure_uses_safe_fallback(self):
        fake_settings = SimpleNamespace(
            ai_provider="groq",
            groq_api_key="test-key",
            groq_api_url="https://groq.example/chat/completions",
            groq_model="test-model",
        )
        response = Mock(status_code=402)
        with patch("app.web.consultation_ai.settings", fake_settings), patch(
            "app.web.consultation_ai.requests.post", return_value=response
        ):
            reply, using_ai = build_consultation_reply(
                "We need help improving our process",
                {
                    "business_type": "coaching centre",
                    "problem_identified": True,
                    "requirements_collected": True,
                    "budget_disclosed": False,
                },
                [{"role": "assistant", "content": "Tell me about the problem."}],
            )
        self.assertIn("what budget have you allocated", reply.lower())
        self.assertFalse(using_ai)

    def test_missing_provider_key_falls_back_without_request(self):
        fake_settings = SimpleNamespace(
            ai_provider="groq",
            groq_api_key=None,
        )
        with patch("app.web.consultation_ai.settings", fake_settings), patch(
            "app.web.consultation_ai.requests.post"
        ) as post:
            reply = provider_reply("I run a shop", {"business_type": "retail"}, [])
        self.assertIsNone(reply)
        post.assert_not_called()


    def test_numeric_budget_answer_gets_contextual_fallback(self):
        with patch(
            "app.web.consultation_ai.provider_reply", return_value=None
        ):
            reply, using_ai = build_consultation_reply(
                "500",
                {
                    "business_type": "IELTS coaching",
                    "problem_identified": False,
                    "requirements_collected": False,
                    "budget_disclosed": False,
                },
                [
                    {
                        "role": "assistant",
                        "content": (
                            "What is your current Meta-ad budget, who are you targeting, "
                            "and how do you track leads?"
                        ),
                    }
                ],
            )

        self.assertIn("500", reply)
        self.assertIn("which currency", reply.lower())
        self.assertIn("daily, monthly, or total", reply.lower())
        self.assertFalse(using_ai)

    def test_http_failure_logs_safe_status_without_secret_or_customer_text(self):
        fake_settings = SimpleNamespace(
            ai_provider="groq",
            groq_api_key="test-secret-key",
            groq_api_url="https://groq.example/chat/completions",
            groq_model="test-model",
        )
        response = Mock(status_code=402)
        customer_message = "PRIVATE_CUSTOMER_MESSAGE_123"

        with patch("app.web.consultation_ai.settings", fake_settings), patch(
            "app.web.consultation_ai.requests.post", return_value=response
        ), self.assertLogs("app.web.consultation_ai", level="WARNING") as captured:
            reply = provider_reply(
                customer_message,
                {"business_type": "IELTS coaching"},
                [],
            )

        self.assertIsNone(reply)
        log_output = "\\n".join(captured.output)
        self.assertIn("provider=Groq", log_output)
        self.assertIn("status=402", log_output)
        self.assertIn("payment_required", log_output)
        self.assertNotIn("test-secret-key", log_output)
        self.assertNotIn(customer_message, log_output)


if __name__ == "__main__":
    unittest.main()
