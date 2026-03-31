import os
import sys
import unittest

def _add_syspath():
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)

_add_syspath()


class ProviderDetectionTests(unittest.TestCase):
    def test_litellm_proxy_forces_chat_mode_for_direct_models(self):
        from app.engines.provider import detect_provider
        p = detect_provider("anthropic/claude-sonnet-4", gateway="litellm_proxy")
        self.assertEqual(p.name, "litellm_proxy")
        self.assertTrue(p.litellm)
        self.assertEqual(p.api_mode, "chat")
        self.assertFalse(p.supports_reasoning)
        self.assertEqual(p.default_base_url, "http://127.0.0.1:4000/v1")

    def test_openrouter_model(self):
        from app.engines.provider import detect_provider
        p = detect_provider("openrouter/openai/gpt-4.1")
        self.assertEqual(p.name, "openrouter")
        self.assertTrue(p.litellm)
        self.assertEqual(p.api_mode, "chat")
        self.assertFalse(p.supports_reasoning)
        self.assertEqual(p.default_base_url, "https://openrouter.ai/api/v1")

    def test_openai_direct(self):
        from app.engines.provider import detect_provider
        p = detect_provider("openai/gpt-4.1")
        self.assertEqual(p.name, "openai")
        self.assertFalse(p.litellm)
        self.assertEqual(p.api_mode, "responses")

    def test_openai_reasoning_model(self):
        from app.engines.provider import detect_provider
        p = detect_provider("openai/o4-mini")
        self.assertEqual(p.name, "openai")
        self.assertTrue(p.supports_reasoning)

    def test_anthropic_direct(self):
        from app.engines.provider import detect_provider
        p = detect_provider("anthropic/claude-sonnet-4")
        self.assertEqual(p.name, "anthropic")
        self.assertTrue(p.litellm)
        self.assertEqual(p.api_mode, "messages")
        self.assertFalse(p.supports_reasoning)

    def test_google_direct(self):
        from app.engines.provider import detect_provider
        p = detect_provider("google/gemini-2.5-flash")
        self.assertEqual(p.name, "google")
        self.assertTrue(p.litellm)
        self.assertEqual(p.api_mode, "chat")

    def test_unknown_provider_defaults(self):
        from app.engines.provider import detect_provider
        p = detect_provider("mistral/mistral-large")
        self.assertEqual(p.name, "mistral")
        self.assertTrue(p.litellm)
        self.assertEqual(p.api_mode, "chat")
        self.assertEqual(p.default_base_url, "")

    def test_empty_model(self):
        from app.engines.provider import detect_provider
        p = detect_provider("")
        self.assertEqual(p.name, "unknown")
        self.assertTrue(p.litellm)

    def test_is_reasoning_model(self):
        from app.engines.provider import is_reasoning_model
        self.assertTrue(is_reasoning_model("openai/o4-mini"))
        self.assertTrue(is_reasoning_model("openai/o1"))
        self.assertTrue(is_reasoning_model("openai/o3"))
        self.assertFalse(is_reasoning_model("openai/gpt-4.1"))
        self.assertFalse(is_reasoning_model("openrouter/openai/o4-mini"))  # openrouter 不传 reasoning


if __name__ == "__main__":
    unittest.main()
