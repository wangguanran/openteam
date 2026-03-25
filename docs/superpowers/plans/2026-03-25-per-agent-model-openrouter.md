# Per-Agent Model Configuration + OpenRouter Default Provider

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable each agent in a workflow to use a different LLM model via OpenRouter, and make OpenRouter the default provider with robust compatibility.

**Architecture:** Extend `WorkflowAgentSpec` with `model/base_url/api_key/max_tokens` fields. The workflow runner resolves per-agent LLM config by merging agent-level overrides onto the global defaults. `${ENV_VAR}` syntax in YAML values is expanded at load time. OpenRouter compatibility is fixed by defaulting to litellm mode (no `responses` API, no `reasoning_effort` for non-reasoning models).

**Tech Stack:** Python 3.11+, CrewAI 1.11, litellm, FastAPI, OpenRouter API

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `engines/crewai/workflow_registry.py` | Modify | Add model/base_url/api_key/max_tokens to WorkflowAgentSpec + env var expansion |
| `engines/llm_config.py` | Modify | Add `build_agent_llm_config()` that merges agent overrides onto global defaults |
| `engines/crewai/workflow_runner.py` | Modify | Per-agent LLM creation in `_execute_engine_task()` and `_execute_crewai_task()` |
| `llm_factory.py` | Modify | Fix OpenRouter: litellm auto-detect from base_url, no responses API, no reasoning_effort |
| `engines/crewai/engine.py` | Modify | Same OpenRouter fixes in CrewAIEngine.build_llm() |
| `teams/repo_improvement/specs/workflows/repo-review.yaml` | Modify | Add per-agent model declarations |
| `tests/test_per_agent_model.py` | Create | Unit tests for per-agent config resolution |
| `tests/test_openrouter_compat.py` | Create | Unit tests for OpenRouter LLM factory compat |

---

### Task 1: Fix OpenRouter compatibility in llm_factory

**Files:**
- Modify: `scaffolds/runtime/orchestrator/app/llm_factory.py:88-106`
- Modify: `scaffolds/runtime/orchestrator/app/engines/crewai/engine.py:27-43`
- Create: `tests/test_openrouter_compat.py`

- [ ] **Step 1: Write failing test for OpenRouter detection**

```python
# tests/test_openrouter_compat.py
import os
import sys
import types
import unittest
from unittest import mock

def _add_syspath():
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)

_add_syspath()

class _FakeLLM:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

class OpenRouterCompatTests(unittest.TestCase):
    def test_openrouter_base_url_enables_litellm(self):
        fake_module = types.SimpleNamespace(LLM=_FakeLLM)
        with mock.patch.dict(os.environ, {
            "OPENTEAM_LLM_MODEL": "openai/gpt-4.1",
            "OPENTEAM_LLM_BASE_URL": "https://openrouter.ai/api/v1",
            "OPENTEAM_LLM_API_KEY": "sk-test",
            "OPENTEAM_CREWAI_AUTH_MODE": "",
        }, clear=False), mock.patch(
            "app.llm_factory.engine_runtime.require_crewai_importable",
            return_value={"importable": True},
        ), mock.patch(
            "app.llm_factory.codex_llm.codex_login_status",
            return_value=(False, ""),
        ), mock.patch.dict(sys.modules, {"crewai.llm": fake_module}):
            from app import llm_factory
            llm = llm_factory.build_crewai_llm()

        self.assertTrue(llm.kwargs["is_litellm"])
        self.assertNotIn("api", llm.kwargs)
        self.assertNotIn("reasoning_effort", llm.kwargs)

    def test_non_openrouter_uses_responses_api(self):
        fake_module = types.SimpleNamespace(LLM=_FakeLLM)
        with mock.patch.dict(os.environ, {
            "OPENTEAM_LLM_MODEL": "openai/gpt-5.4",
            "OPENTEAM_LLM_BASE_URL": "",
            "OPENTEAM_LLM_API_KEY": "sk-test",
            "OPENTEAM_CREWAI_AUTH_MODE": "",
        }, clear=False), mock.patch(
            "app.llm_factory.engine_runtime.require_crewai_importable",
            return_value={"importable": True},
        ), mock.patch(
            "app.llm_factory.codex_llm.codex_login_status",
            return_value=(False, ""),
        ), mock.patch.dict(sys.modules, {"crewai.llm": fake_module}):
            from app import llm_factory
            llm = llm_factory.build_crewai_llm()

        self.assertEqual(llm.kwargs["api"], "responses")

    def test_max_tokens_from_env(self):
        fake_module = types.SimpleNamespace(LLM=_FakeLLM)
        with mock.patch.dict(os.environ, {
            "OPENTEAM_LLM_MODEL": "openai/gpt-4.1",
            "OPENTEAM_LLM_BASE_URL": "https://openrouter.ai/api/v1",
            "OPENTEAM_LLM_API_KEY": "sk-test",
            "OPENTEAM_LLM_MAX_TOKENS": "32768",
            "OPENTEAM_CREWAI_AUTH_MODE": "",
        }, clear=False), mock.patch(
            "app.llm_factory.engine_runtime.require_crewai_importable",
            return_value={"importable": True},
        ), mock.patch(
            "app.llm_factory.codex_llm.codex_login_status",
            return_value=(False, ""),
        ), mock.patch.dict(sys.modules, {"crewai.llm": fake_module}):
            from app import llm_factory
            llm = llm_factory.build_crewai_llm()

        self.assertEqual(llm.kwargs["max_tokens"], 32768)

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/.openteam/runtime/default/cache/py-venv/bin/python -m pytest tests/test_openrouter_compat.py -v`
Expected: FAIL (current code always sets `api="responses"` and detects litellm only from model name)

- [ ] **Step 3: Fix llm_factory.py**

In `scaffolds/runtime/orchestrator/app/llm_factory.py`, replace lines 88-106:

```python
    use_litellm = "openrouter" in model.lower() or "openrouter" in base_url.lower()
    kwargs: dict[str, Any] = {
        "model": model,
        "is_litellm": use_litellm,
        "max_tokens": int(os.getenv("OPENTEAM_LLM_MAX_TOKENS") or "16384"),
    }
    if not use_litellm:
        kwargs["api"] = "responses"
    max_retries_raw = str(os.getenv("OPENTEAM_CREWAI_MAX_RETRIES") or "").strip()
    if max_retries_raw:
        try:
            kwargs["max_retries"] = max(0, int(max_retries_raw))
        except Exception:
            pass
    if not use_litellm and any(token in model.lower() for token in ("gpt-5", "codex", "o1", "o3", "o4")):
        kwargs["reasoning_effort"] = reasoning_effort
    if base_url:
        kwargs["base_url"] = base_url
    if api_key:
        kwargs["api_key"] = api_key
    return LLM(**kwargs)
```

- [ ] **Step 4: Apply same fix to engines/crewai/engine.py:build_llm()**

Replace the kwargs block:

```python
        use_litellm = "openrouter" in config.model.lower() or "openrouter" in config.base_url.lower()
        kwargs: dict[str, Any] = {
            "model": config.model,
            "max_tokens": config.max_tokens,
            "is_litellm": use_litellm,
        }
        if not use_litellm:
            kwargs["api"] = "responses"
        if config.max_retries:
            kwargs["max_retries"] = config.max_retries
        if not use_litellm and any(t in config.model.lower() for t in ("gpt-5", "codex", "o1", "o3", "o4")):
            kwargs["reasoning_effort"] = config.reasoning_effort
```

- [ ] **Step 5: Run test to verify it passes**

Run: `~/.openteam/runtime/default/cache/py-venv/bin/python -m pytest tests/test_openrouter_compat.py -v`
Expected: 3 PASSED

- [ ] **Step 6: Run full test suite**

Run: `~/.openteam/runtime/default/cache/py-venv/bin/python -m pytest tests/ -q`
Expected: 157+ passed, 0 failed

- [ ] **Step 7: Commit**

```bash
git add tests/test_openrouter_compat.py scaffolds/runtime/orchestrator/app/llm_factory.py scaffolds/runtime/orchestrator/app/engines/crewai/engine.py
git commit -m "fix: OpenRouter compat - auto-detect litellm from base_url, skip responses API and reasoning_effort"
```

---

### Task 2: Add model/base_url/api_key/max_tokens to WorkflowAgentSpec

**Files:**
- Modify: `scaffolds/runtime/orchestrator/app/engines/crewai/workflow_registry.py:142-151,342-351`
- Create: `tests/test_per_agent_model.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_per_agent_model.py
import os
import sys
import unittest

def _add_syspath():
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)

_add_syspath()

from app.engines.crewai import workflow_registry

class PerAgentModelTests(unittest.TestCase):
    def test_workflow_agents_have_model_fields(self):
        spec = workflow_registry.workflow_spec("repo-review", project_id="openteam")
        bug_scanner = next(a for a in spec.agents if a.agent_id == "bug_scanner")
        self.assertTrue(hasattr(bug_scanner, "model"))
        self.assertTrue(hasattr(bug_scanner, "base_url"))
        self.assertTrue(hasattr(bug_scanner, "api_key"))
        self.assertTrue(hasattr(bug_scanner, "max_tokens"))

    def test_agent_model_defaults_to_empty(self):
        spec = workflow_registry.workflow_spec("repo-review", project_id="openteam")
        scanner = next(a for a in spec.agents if a.agent_id == "bug_scanner")
        # Until YAML is updated, model should default to ""
        self.assertIsInstance(scanner.model, str)

    def test_env_var_expansion_in_api_key(self):
        from app.engines.crewai.workflow_registry import _agent_spec_from_doc
        os.environ["TEST_API_KEY"] = "sk-expanded-test"
        try:
            doc = {
                "agent_id": "test",
                "role_id": "Test",
                "api_key": "${TEST_API_KEY}",
            }
            agent = _agent_spec_from_doc(doc)
            self.assertEqual(agent.api_key, "sk-expanded-test")
        finally:
            os.environ.pop("TEST_API_KEY", None)

    def test_env_var_not_found_stays_literal(self):
        from app.engines.crewai.workflow_registry import _agent_spec_from_doc
        doc = {
            "agent_id": "test",
            "role_id": "Test",
            "api_key": "${NONEXISTENT_KEY_12345}",
        }
        agent = _agent_spec_from_doc(doc)
        self.assertEqual(agent.api_key, "")  # unresolved env -> empty

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/.openteam/runtime/default/cache/py-venv/bin/python -m pytest tests/test_per_agent_model.py -v`
Expected: FAIL (WorkflowAgentSpec has no model/base_url/api_key/max_tokens)

- [ ] **Step 3: Add fields to WorkflowAgentSpec**

In `workflow_registry.py`, update the dataclass (line 142):

```python
@dataclass(frozen=True)
class WorkflowAgentSpec:
    agent_id: str
    role_id: str
    tool_profile: str = ""
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    max_tokens: int = 0
    allow_delegation: bool = False
    template_role_id: str = ""
    goal: str = ""
    backstory: str = ""
```

- [ ] **Step 4: Add env var expansion helper and update _agent_spec_from_doc**

Add helper function before `_agent_spec_from_doc` (line ~340):

```python
import re as _re

def _expand_env(value: str) -> str:
    """Expand ${ENV_VAR} references in a string. Unresolved vars become empty."""
    def _replacer(match: _re.Match) -> str:
        return os.environ.get(match.group(1), "")
    return _re.sub(r"\$\{(\w+)\}", _replacer, str(value or ""))
```

Update `_agent_spec_from_doc`:

```python
def _agent_spec_from_doc(raw: dict[str, Any]) -> WorkflowAgentSpec:
    return WorkflowAgentSpec(
        agent_id=str(raw.get("agent_id") or raw.get("id") or "").strip(),
        role_id=str(raw.get("role_id") or raw.get("role") or "").strip(),
        tool_profile=str(raw.get("tool_profile") or "").strip(),
        model=str(raw.get("model") or "").strip(),
        base_url=_expand_env(str(raw.get("base_url") or "").strip()),
        api_key=_expand_env(str(raw.get("api_key") or "").strip()),
        max_tokens=_to_int(raw.get("max_tokens"), 0, minimum=0),
        allow_delegation=_to_bool(raw.get("allow_delegation"), False),
        template_role_id=str(raw.get("template_role_id") or "").strip(),
        goal=str(raw.get("goal") or "").strip(),
        backstory=str(raw.get("backstory") or "").strip(),
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `~/.openteam/runtime/default/cache/py-venv/bin/python -m pytest tests/test_per_agent_model.py -v`
Expected: 4 PASSED

- [ ] **Step 6: Run full test suite**

Run: `~/.openteam/runtime/default/cache/py-venv/bin/python -m pytest tests/ -q`
Expected: 161+ passed, 0 failed

- [ ] **Step 7: Commit**

```bash
git add scaffolds/runtime/orchestrator/app/engines/crewai/workflow_registry.py tests/test_per_agent_model.py
git commit -m "feat: add model/base_url/api_key/max_tokens to WorkflowAgentSpec with env var expansion"
```

---

### Task 3: Add build_agent_llm_config() to llm_config.py

**Files:**
- Modify: `scaffolds/runtime/orchestrator/app/engines/llm_config.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_per_agent_model.py`:

```python
class AgentLLMConfigTests(unittest.TestCase):
    def test_agent_override_model(self):
        from app.engines.llm_config import build_agent_llm_config
        from app.engines.crewai.workflow_registry import WorkflowAgentSpec
        agent = WorkflowAgentSpec(agent_id="t", role_id="T", model="google/gemini-2.5-flash")
        cfg = build_agent_llm_config(agent_spec=agent)
        self.assertEqual(cfg.model, "google/gemini-2.5-flash")

    def test_agent_inherits_global_when_empty(self):
        from app.engines.llm_config import build_agent_llm_config
        from app.engines.crewai.workflow_registry import WorkflowAgentSpec
        agent = WorkflowAgentSpec(agent_id="t", role_id="T")
        with unittest.mock.patch.dict(os.environ, {"OPENTEAM_LLM_MODEL": "openai/gpt-4.1"}):
            cfg = build_agent_llm_config(agent_spec=agent)
        self.assertEqual(cfg.model, "openai/gpt-4.1")

    def test_agent_override_max_tokens(self):
        from app.engines.llm_config import build_agent_llm_config
        from app.engines.crewai.workflow_registry import WorkflowAgentSpec
        agent = WorkflowAgentSpec(agent_id="t", role_id="T", max_tokens=32768)
        cfg = build_agent_llm_config(agent_spec=agent)
        self.assertEqual(cfg.max_tokens, 32768)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/.openteam/runtime/default/cache/py-venv/bin/python -m pytest tests/test_per_agent_model.py::AgentLLMConfigTests -v`
Expected: FAIL (build_agent_llm_config doesn't exist)

- [ ] **Step 3: Implement build_agent_llm_config()**

Add to `engines/llm_config.py`:

```python
def build_agent_llm_config(*, agent_spec: Any = None, workflow: Any = None) -> EngineLLMConfig:
    """Build LLM config with per-agent overrides merged onto global defaults."""
    base = build_llm_config(workflow=workflow)
    if agent_spec is None:
        return base
    agent_model = str(getattr(agent_spec, "model", "") or "").strip()
    agent_base_url = str(getattr(agent_spec, "base_url", "") or "").strip()
    agent_api_key = str(getattr(agent_spec, "api_key", "") or "").strip()
    agent_max_tokens = int(getattr(agent_spec, "max_tokens", 0) or 0)
    return EngineLLMConfig(
        model=agent_model or base.model,
        base_url=agent_base_url or base.base_url,
        api_key=agent_api_key or base.api_key,
        reasoning_effort=base.reasoning_effort,
        max_tokens=agent_max_tokens if agent_max_tokens > 0 else base.max_tokens,
        max_retries=base.max_retries,
        extra=base.extra,
    )
```

Also fix line 61: `max_tokens=4096` → `max_tokens=max_tokens` (existing bug).

- [ ] **Step 4: Run test to verify it passes**

Run: `~/.openteam/runtime/default/cache/py-venv/bin/python -m pytest tests/test_per_agent_model.py -v`
Expected: 7 PASSED

- [ ] **Step 5: Commit**

```bash
git add scaffolds/runtime/orchestrator/app/engines/llm_config.py tests/test_per_agent_model.py
git commit -m "feat: add build_agent_llm_config() for per-agent LLM overrides"
```

---

### Task 4: Wire per-agent LLM into workflow_runner

**Files:**
- Modify: `scaffolds/runtime/orchestrator/app/engines/crewai/workflow_runner.py:285-355`

- [ ] **Step 1: Update _execute_engine_task() to use per-agent config**

Replace lines 335-336 in `_execute_engine_task()`:

```python
    # OLD:
    # llm_config = build_llm_config(workflow=context.workflow)
    # llm = engine.build_llm(llm_config)

    # NEW: per-agent LLM config
    from app.engines.llm_config import build_agent_llm_config
    llm_config = build_agent_llm_config(agent_spec=agent_spec_raw, workflow=context.workflow)
    llm = engine.build_llm(llm_config)
```

- [ ] **Step 2: Update _execute_crewai_task() similarly**

Replace line 243:

```python
    # OLD:
    # llm = llm_factory.build_crewai_llm(workflow=context.workflow)

    # NEW: per-agent LLM
    from app.engines.llm_config import build_agent_llm_config
    agent_llm_config = build_agent_llm_config(agent_spec=agent_spec, workflow=context.workflow)
    llm = llm_factory.build_crewai_llm(workflow=context.workflow, override_config=agent_llm_config)
```

Also update `llm_factory.build_crewai_llm()` to accept optional `override_config`:

```python
def build_crewai_llm(*, workflow=None, override_config=None):
    # ... existing logic ...
    if override_config is not None:
        if override_config.model:
            model = override_config.model
        if override_config.base_url:
            base_url = override_config.base_url
        if override_config.api_key:
            api_key = override_config.api_key
        if override_config.max_tokens:
            # apply below in kwargs
            pass
    # ... rest of function
```

- [ ] **Step 3: Run full test suite**

Run: `~/.openteam/runtime/default/cache/py-venv/bin/python -m pytest tests/ -q`
Expected: 161+ passed, 0 failed

- [ ] **Step 4: Commit**

```bash
git add scaffolds/runtime/orchestrator/app/engines/crewai/workflow_runner.py scaffolds/runtime/orchestrator/app/llm_factory.py
git commit -m "feat: per-agent LLM creation in workflow_runner via build_agent_llm_config"
```

---

### Task 5: Update repo-review.yaml with per-agent models

**Files:**
- Modify: `scaffolds/runtime/orchestrator/app/teams/repo_improvement/specs/workflows/repo-review.yaml`

- [ ] **Step 1: Add model declarations to agents**

Update the agents section:

```yaml
agents:
  - agent_id: bug_scanner
    role_id: Test-Manager
    tool_profile: qa
    model: anthropic/claude-sonnet-4
    max_tokens: 16384
    backstory: >
      You reason like a QA/test lead...

  - agent_id: feature_scanner
    role_id: Product-Manager
    model: openai/gpt-4.1
    max_tokens: 8192
    backstory: >
      You think like a product manager...

  - agent_id: test_gap_scanner
    role_id: Test-Case-Gap-Agent
    model: openai/gpt-4.1-mini
    max_tokens: 8192
    backstory: ...

  - agent_id: quality_scanner
    role_id: Code-Quality-Analyst
    model: openai/gpt-4.1-mini
    max_tokens: 8192
    backstory: ...

  - agent_id: process_scanner
    role_id: Process-Optimization-Analyst
    model: openai/gpt-4.1-mini
    max_tokens: 8192
    backstory: ...

  - agent_id: drafter
    role_id: Issue-Drafter
    model: openai/gpt-4.1
    max_tokens: 32768
    backstory: ...

  - agent_id: reviewer
    role_id: Plan-Review-Agent
    model: anthropic/claude-sonnet-4
    max_tokens: 16384
    backstory: ...

  - agent_id: qa_gate
    role_id: Plan-QA-Agent
    model: openai/gpt-4.1-mini
    max_tokens: 16384
    backstory: ...
```

- [ ] **Step 2: Verify YAML loads correctly**

Run: `~/.openteam/runtime/default/cache/py-venv/bin/python -c "
from app.engines.crewai import workflow_registry
spec = workflow_registry.workflow_spec('repo-review', project_id='openteam')
for a in spec.agents:
    print(f'{a.agent_id:25s} model={a.model:35s} max_tokens={a.max_tokens}')
"`

Expected: Each agent shows its configured model.

- [ ] **Step 3: Run full test suite**

Run: `~/.openteam/runtime/default/cache/py-venv/bin/python -m pytest tests/ -q`
Expected: 161+ passed, 0 failed

- [ ] **Step 4: Commit**

```bash
git add scaffolds/runtime/orchestrator/app/teams/repo_improvement/specs/workflows/repo-review.yaml
git commit -m "feat: configure per-agent models in repo-review workflow (Claude for scan/review, GPT for draft, mini for QA)"
```

---

### Task 6: Docker end-to-end validation

**Files:**
- Modify: `scaffolds/runtime/docker-compose.override.yaml`

- [ ] **Step 1: Update docker-compose.override.yaml**

Set default env vars for OpenRouter:

```yaml
services:
  control-plane:
    environment:
      PYTHONPATH: /openteam/scaffolds/runtime/orchestrator:/openteam
      OPENTEAM_LLM_MODEL: openai/gpt-4.1
      OPENTEAM_LLM_BASE_URL: https://openrouter.ai/api/v1
      OPENTEAM_LLM_API_KEY: ${OPENTEAM_LLM_API_KEY}
      OPENTEAM_LLM_MAX_TOKENS: "16384"
```

- [ ] **Step 2: Rebuild and start Docker stack**

```bash
docker compose -f scaffolds/runtime/docker-compose.yml \
  -f scaffolds/runtime/docker-compose.override.yaml \
  --env-file /tmp/openteam-docker.env \
  -p openteam-runtime down control-plane && \
docker compose ... up -d control-plane
```

- [ ] **Step 3: Trigger repo-review and verify findings**

```bash
curl -X POST http://127.0.0.1:8787/v1/teams/repo-improvement/run \
  -H "Content-Type: application/json" \
  -d '{"project_id":"openteam","repo_path":"/openteam","force":true}'
```

Expected: `ok=true`, `bug_findings > 0` or `pending_proposals > 0`

- [ ] **Step 4: Commit all remaining changes**

```bash
git add -A
git commit -m "feat: MS-1 complete - OpenRouter default provider with per-agent model support"
git push
```
