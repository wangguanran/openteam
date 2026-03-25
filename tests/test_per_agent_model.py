import os
import sys
import unittest
from unittest import mock

def _add_syspath():
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)

_add_syspath()

from app.engines.crewai import workflow_registry


class WorkflowAgentSpecFieldsTests(unittest.TestCase):
    def test_agent_spec_has_model_fields(self):
        spec = workflow_registry.WorkflowAgentSpec(agent_id="t", role_id="T")
        self.assertEqual(spec.model, "")
        self.assertEqual(spec.base_url, "")
        self.assertEqual(spec.api_key, "")
        self.assertEqual(spec.max_tokens, 0)

    def test_agent_spec_from_doc_parses_model(self):
        doc = {"agent_id": "scanner", "role_id": "Test-Manager", "model": "openrouter/openai/gpt-4.1", "max_tokens": 16384}
        agent = workflow_registry._agent_spec_from_doc(doc)
        self.assertEqual(agent.model, "openrouter/openai/gpt-4.1")
        self.assertEqual(agent.max_tokens, 16384)

    def test_env_var_expansion_in_api_key(self):
        os.environ["TEST_AGENT_KEY"] = "sk-expanded"
        try:
            doc = {"agent_id": "t", "role_id": "T", "api_key": "${TEST_AGENT_KEY}"}
            agent = workflow_registry._agent_spec_from_doc(doc)
            self.assertEqual(agent.api_key, "sk-expanded")
        finally:
            os.environ.pop("TEST_AGENT_KEY", None)

    def test_env_var_not_found_resolves_empty(self):
        doc = {"agent_id": "t", "role_id": "T", "api_key": "${NONEXISTENT_12345}"}
        agent = workflow_registry._agent_spec_from_doc(doc)
        self.assertEqual(agent.api_key, "")

    def test_env_var_expansion_in_base_url(self):
        os.environ["TEST_BASE_URL"] = "https://custom.api.com/v1"
        try:
            doc = {"agent_id": "t", "role_id": "T", "base_url": "${TEST_BASE_URL}"}
            agent = workflow_registry._agent_spec_from_doc(doc)
            self.assertEqual(agent.base_url, "https://custom.api.com/v1")
        finally:
            os.environ.pop("TEST_BASE_URL", None)

    def test_repo_review_agents_have_model_field(self):
        spec = workflow_registry.workflow_spec("repo-review", project_id="openteam")
        for agent in spec.agents:
            self.assertTrue(hasattr(agent, "model"), f"agent {agent.agent_id} missing model field")
            self.assertTrue(hasattr(agent, "max_tokens"), f"agent {agent.agent_id} missing max_tokens field")


class AgentLLMConfigTests(unittest.TestCase):
    def test_agent_model_overrides_global(self):
        from app.engines.llm_config import build_agent_llm_config
        agent = workflow_registry.WorkflowAgentSpec(agent_id="t", role_id="T", model="openrouter/google/gemini-2.5-flash")
        with mock.patch.dict(os.environ, {"OPENTEAM_LLM_MODEL": "openrouter/openai/gpt-4.1"}):
            cfg = build_agent_llm_config(agent_spec=agent)
        self.assertEqual(cfg.model, "openrouter/google/gemini-2.5-flash")

    def test_empty_agent_model_inherits_global(self):
        from app.engines.llm_config import build_agent_llm_config
        agent = workflow_registry.WorkflowAgentSpec(agent_id="t", role_id="T")
        with mock.patch.dict(os.environ, {"OPENTEAM_LLM_MODEL": "openrouter/openai/gpt-4.1"}):
            cfg = build_agent_llm_config(agent_spec=agent)
        self.assertEqual(cfg.model, "openrouter/openai/gpt-4.1")

    def test_agent_max_tokens_overrides(self):
        from app.engines.llm_config import build_agent_llm_config
        agent = workflow_registry.WorkflowAgentSpec(agent_id="t", role_id="T", max_tokens=32768)
        cfg = build_agent_llm_config(agent_spec=agent)
        self.assertEqual(cfg.max_tokens, 32768)

    def test_agent_api_key_overrides(self):
        from app.engines.llm_config import build_agent_llm_config
        agent = workflow_registry.WorkflowAgentSpec(agent_id="t", role_id="T", api_key="sk-agent-specific")
        with mock.patch.dict(os.environ, {"OPENTEAM_LLM_API_KEY": "sk-global"}):
            cfg = build_agent_llm_config(agent_spec=agent)
        self.assertEqual(cfg.api_key, "sk-agent-specific")

    def test_none_agent_returns_global(self):
        from app.engines.llm_config import build_agent_llm_config
        cfg = build_agent_llm_config(agent_spec=None)
        self.assertIsNotNone(cfg.model)


if __name__ == "__main__":
    unittest.main()
