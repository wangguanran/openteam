import importlib.util
import os
import sys
import threading
import types
import unittest


def _add_template_app_to_syspath() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()

if "agents" not in sys.modules:
    agents_mod = types.ModuleType("agents")

    class _DummyAgent:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

    agents_mod.Agent = _DummyAgent
    sys.modules["agents"] = agents_mod

FASTAPI_AVAILABLE = importlib.util.find_spec("fastapi") is not None
if FASTAPI_AVAILABLE:
    from app import main as app_main  # noqa: E402
else:  # pragma: no cover
    app_main = None


@unittest.skipUnless(FASTAPI_AVAILABLE, "fastapi is not available in this test environment")
class RepoImprovementLoopConcurrencyTests(unittest.TestCase):
    def test_loop_worker_concurrency_defaults_to_ten(self) -> None:
        original = os.environ.pop("TEAMOS_REPO_IMPROVEMENT_DISCOVERY_CONCURRENCY", None)
        try:
            self.assertEqual(
                app_main._repo_improvement_loop_worker_concurrency(
                    "TEAMOS_REPO_IMPROVEMENT_DISCOVERY_CONCURRENCY",
                    10,
                ),
                10,
            )
        finally:
            if original is not None:
                os.environ["TEAMOS_REPO_IMPROVEMENT_DISCOVERY_CONCURRENCY"] = original

    def test_target_job_pool_runs_jobs_concurrently(self) -> None:
        targets = [{"target_id": "a"}, {"target_id": "b"}]
        gate = threading.Event()
        lock = threading.Lock()
        active = 0
        peak = 0

        def _job(target: dict[str, str]) -> str:
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active >= 2:
                    gate.set()
            gate.wait(1)
            with lock:
                active -= 1
            return str(target.get("target_id") or "")

        results, errors = app_main._run_repo_improvement_target_jobs_in_pool(
            targets=targets,
            worker_concurrency=10,
            thread_name_prefix="repo-improvement-test",
            job_fn=_job,
        )

        self.assertFalse(errors)
        self.assertCountEqual(results, ["a", "b"])
        self.assertGreaterEqual(peak, 2)
