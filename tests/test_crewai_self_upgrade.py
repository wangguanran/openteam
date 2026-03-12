import json
import os
import subprocess
import sys
import tempfile
import unittest
import datetime as dt
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock


def _add_template_app_to_syspath() -> None:
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_dir = os.path.join(repo_root, "scaffolds", "runtime", "orchestrator")
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)


_add_template_app_to_syspath()
os.environ.setdefault("TEAMOS_REPO_IMPROVEMENT_LOCALIZE_ZH", "0")

from app import crewai_self_upgrade, improvement_store, plan_store  # noqa: E402


class _FakeDB:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.assignments: list[dict] = []
        self._n = 0

    def register_agent(self, **kwargs):
        self._n += 1
        return f"agent-{self._n}"

    def update_assignment(self, **kwargs):
        self.assignments.append(kwargs)

    def add_event(self, **kwargs):
        self.events.append(kwargs)


class CrewAISelfUpgradeTests(unittest.TestCase):
    def _make_self_upgrade_spec(self, repo: Path, *, force: bool = False, dry_run: bool = True) -> SimpleNamespace:
        return SimpleNamespace(
            project_id="teamos",
            workstream_id="general",
            repo_path=str(repo),
            repo_locator="foo/bar",
            force=force,
            dry_run=dry_run,
            trigger="test",
            task_id="",
        )

    def _repo_context(self, repo: Path, *, head_commit: str) -> dict[str, object]:
        return {
            "repo_root": str(repo),
            "repo_locator": "foo/bar",
            "current_version": "0.1.0",
            "default_branch": "main",
            "head_commit": head_commit,
            "git_status_dirty": False,
            "repo_name": repo.name,
        }

    def _bug_plan(self, *, title: str = "Fix runtime regression") -> crewai_self_upgrade.UpgradePlan:
        return crewai_self_upgrade.UpgradePlan(
            summary=title,
            findings=[
                crewai_self_upgrade.UpgradeFinding(
                    kind="BUG",
                    lane="bug",
                    title=title,
                    summary="Current runtime path still fails under a reproducible defect signal.",
                    workstream_id="general",
                    version_bump="patch",
                    target_version="0.1.1",
                    tests=["python -m unittest tests.test_runtime_bug"],
                    acceptance=["Runtime defect no longer reproduces"],
                    work_items=[
                        crewai_self_upgrade.UpgradeWorkItem(
                            title=title,
                            summary="Repair the current runtime regression.",
                            owner_role=crewai_self_upgrade.ROLE_BUGFIX_CODING_AGENT,
                            review_role=crewai_self_upgrade.ROLE_REVIEW_AGENT,
                            qa_role=crewai_self_upgrade.ROLE_QA_AGENT,
                            workstream_id="general",
                            allowed_paths=["src/runtime_bug.py"],
                            tests=["python -m unittest tests.test_runtime_bug"],
                            acceptance=["Runtime defect no longer reproduces"],
                            worktree_hint="/tmp/worktrees/runtime-bug",
                            module="Runtime",
                        )
                    ],
                )
            ],
        )

    def test_parse_repo_locator_supports_https_and_ssh(self):
        self.assertEqual(crewai_self_upgrade._parse_repo_locator("https://github.com/foo/bar.git"), "foo/bar")
        self.assertEqual(crewai_self_upgrade._parse_repo_locator("git@github.com:foo/bar.git"), "foo/bar")

    def test_collect_repo_context_marks_dirty_repo_when_git_status_has_output(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / ".git").mkdir()
            (repo / "README.md").write_text("# Demo\n", encoding="utf-8")

            fake_proc = SimpleNamespace(returncode=0, stdout=" M src/demo.py\n", stderr="")
            with mock.patch("app.crewai_self_upgrade.subprocess.run", return_value=fake_proc):
                ctx = crewai_self_upgrade.collect_repo_context(repo_root=repo)

            self.assertTrue(ctx["git_status_dirty"])
            self.assertEqual(ctx["git_status_sample"], [" M src/demo.py"])

    def test_collect_repo_context_includes_repository_inspection(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            (repo / "README.md").write_text("# Demo\n\nruntime repo\n", encoding="utf-8")
            (repo / "pyproject.toml").write_text("[project]\nname='demo'\nversion='0.1.0'\n", encoding="utf-8")
            (repo / "src").mkdir()
            (repo / "tests").mkdir()
            (repo / "src" / "__main__.py").write_text("print('hello')\n", encoding="utf-8")
            (repo / "tests" / "test_demo.py").write_text(
                "import unittest\n\n\nclass DemoTests(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "."], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

            ctx = crewai_self_upgrade.collect_repo_context(repo_root=repo)

            inspection = ctx.get("repository_inspection") or {}
            self.assertGreaterEqual(int(inspection.get("tracked_file_count") or 0), 4)
            self.assertIn("README.md", inspection.get("tracked_file_sample") or [])
            self.assertIn("pytest -q", inspection.get("test_command_candidates") or [])
            focus_paths = [str(item.get("path") or "") for item in (inspection.get("focus_file_excerpts") or []) if isinstance(item, dict)]
            self.assertIn("README.md", focus_paths)
            self.assertIn("src/__main__.py", focus_paths)
            category_counts = inspection.get("category_counts") or {}
            self.assertGreaterEqual(int(category_counts.get("source") or 0), 1)
            self.assertGreaterEqual(int(category_counts.get("test") or 0), 1)
            baseline_checks = inspection.get("baseline_checks") or []
            self.assertGreaterEqual(len(baseline_checks), 1)
            self.assertEqual(str(baseline_checks[0].get("command") or ""), "python -m unittest")
            self.assertEqual(str(baseline_checks[0].get("status") or ""), "passed")
            text_pass = inspection.get("text_pass") or {}
            self.assertGreaterEqual(int(text_pass.get("text_file_count") or 0), 4)
            excerpt_paths = [str(item.get("path") or "") for item in (text_pass.get("entries") or []) if isinstance(item, dict)]
            self.assertIn("README.md", excerpt_paths)
            self.assertIn("src/__main__.py", excerpt_paths)
            module_chunks = inspection.get("module_chunks") or []
            self.assertGreaterEqual(len(module_chunks), 1)
            first_chunk = module_chunks[0]
            self.assertTrue(str(first_chunk.get("module") or "").strip())
            self.assertGreaterEqual(int(first_chunk.get("included_file_count") or 0), 1)
            self.assertTrue(isinstance(first_chunk.get("files"), list))

    def test_kickoff_upgrade_plan_uses_structured_fast_path_for_bug_only(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / ".git").mkdir()
            repo_context = {
                **self._repo_context(repo, head_commit="abc123"),
                "repository_inspection": {"tracked_file_count": 4, "category_counts": {"source": 2}, "baseline_checks": []},
            }
            bug_scan = crewai_self_upgrade.StructuredBugScanResult(
                summary="已在 src/plugins 模块发现可证明缺陷。",
                findings=[
                    crewai_self_upgrade.StructuredBugCandidate(
                        title="修复插件初始化缺陷",
                        summary="插件初始化路径在当前输入下会稳定失败。",
                        module="Runtime",
                        rationale="基线检查和模块全文阅读均指向初始化异常。",
                        files=["src/plugins/init.py"],
                        tests=["python -m unittest tests.test_plugins"],
                        acceptance=["插件初始化后不再报错"],
                        reproduction_steps=["运行插件初始化入口"],
                        test_case_files=["tests/test_plugins.py"],
                        verification_steps=["重新运行插件初始化测试"],
                    )
                ],
                ci_actions=["为插件初始化回归补充稳定测试命令"],
                notes=["bug-only 快路径已启用"],
            )
            with mock.patch(
                "app.crewai_self_upgrade._enabled_planning_workflow_ids",
                return_value=[crewai_self_upgrade.crewai_role_registry.WORKFLOW_BUG_FIX],
            ), mock.patch(
                "app.crewai_self_upgrade._use_bug_only_fast_path",
                return_value=True,
            ), mock.patch(
                "app.crewai_self_upgrade._bug_scan_module_chunks",
                return_value=[{"module": "src/plugins", "files": [{"path": "src/plugins/init.py", "content": "print('x')"}]}],
            ), mock.patch(
                "app.crewai_self_upgrade._structured_bug_scan_for_chunk",
                return_value=(
                    bug_scan,
                    {"name": "qa_bug_scan_src_plugins", "agent": crewai_self_upgrade.ROLE_TEST_MANAGER, "raw": '{"summary":"ok"}'},
                ),
            ), mock.patch(
                "app.crewai_self_upgrade._crewai_llm",
                return_value=object(),
            ):
                plan, debug = crewai_self_upgrade.kickoff_upgrade_plan(
                    repo_context=repo_context,
                    project_id="projectmanager",
                    max_findings=3,
                    verbose=False,
                )

            self.assertEqual(len(plan.findings), 1)
            self.assertEqual(plan.findings[0].lane, "bug")
            self.assertTrue(any(item.get("name") == "qa_bug_scan_src_plugins" for item in debug.get("task_outputs") or []))
            self.assertTrue(any(item.get("name") == "structured_bug_plan" for item in debug.get("task_outputs") or []))

    def test_kickoff_upgrade_plan_bug_only_progress_callback_receives_partial_logs(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / ".git").mkdir()
            repo_context = {
                **self._repo_context(repo, head_commit="abc123"),
                "repository_inspection": {"tracked_file_count": 4, "category_counts": {"source": 2}, "baseline_checks": []},
            }
            bug_scan = crewai_self_upgrade.StructuredBugScanResult(
                summary="已在 src/plugins 模块发现可证明缺陷。",
                findings=[],
                ci_actions=["补一条 smoke test"],
                notes=["module chunk completed"],
            )
            progress_updates: list[dict[str, object]] = []
            with mock.patch(
                "app.crewai_self_upgrade._enabled_planning_workflow_ids",
                return_value=[crewai_self_upgrade.crewai_role_registry.WORKFLOW_BUG_FIX],
            ), mock.patch(
                "app.crewai_self_upgrade._use_bug_only_fast_path",
                return_value=True,
            ), mock.patch(
                "app.crewai_self_upgrade._bug_scan_module_chunks",
                return_value=[{"module": "src/plugins", "files": [{"path": "src/plugins/init.py", "content": "print('x')"}]}],
            ), mock.patch(
                "app.crewai_self_upgrade._structured_bug_scan_for_chunk",
                return_value=(
                    bug_scan,
                    {"name": "qa_bug_scan_src_plugins", "agent": crewai_self_upgrade.ROLE_TEST_MANAGER, "raw": '{"summary":"ok"}'},
                ),
            ), mock.patch(
                "app.crewai_self_upgrade._crewai_llm",
                return_value=object(),
            ):
                crewai_self_upgrade.kickoff_upgrade_plan(
                    repo_context=repo_context,
                    project_id="projectmanager",
                    max_findings=3,
                    verbose=False,
                    progress_callback=lambda payload: progress_updates.append(payload),
                )

            self.assertEqual(len(progress_updates), 1)
            self.assertEqual(str(progress_updates[0].get("module") or ""), "src/plugins")
            self.assertTrue(
                any(
                    item.get("name") == "qa_bug_scan_src_plugins"
                    for item in ((progress_updates[0].get("crew_debug") or {}).get("task_outputs") or [])
                )
            )

    def test_kickoff_upgrade_plan_bug_only_scans_multiple_module_chunks(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / ".git").mkdir()
            repo_context = {
                **self._repo_context(repo, head_commit="abc123"),
                "repository_inspection": {"tracked_file_count": 6, "category_counts": {"source": 3}, "baseline_checks": []},
            }
            first_scan = crewai_self_upgrade.StructuredBugScanResult(
                summary="src/plugins 模块未发现可证明 bug。",
                findings=[],
                ci_actions=["补一条 smoke test"],
                notes=["first module completed"],
            )
            second_scan = crewai_self_upgrade.StructuredBugScanResult(
                summary="src/core 模块发现可证明缺陷。",
                findings=[
                    crewai_self_upgrade.StructuredBugCandidate(
                        title="修复核心路径缺陷",
                        summary="核心路径在当前输入下会稳定失败。",
                        module="Runtime",
                        rationale="模块全文阅读和基线检查均指向核心路径异常。",
                        files=["src/core/main.py"],
                        tests=["python -m unittest tests.test_core"],
                        acceptance=["核心路径不再抛错"],
                        reproduction_steps=["运行核心路径入口"],
                        test_case_files=["tests/test_core.py"],
                        verification_steps=["重新运行核心路径测试"],
                    )
                ],
                ci_actions=["补充核心路径回归测试"],
                notes=["second module completed"],
            )
            with mock.patch(
                "app.crewai_self_upgrade._enabled_planning_workflow_ids",
                return_value=[crewai_self_upgrade.crewai_role_registry.WORKFLOW_BUG_FIX],
            ), mock.patch(
                "app.crewai_self_upgrade._use_bug_only_fast_path",
                return_value=True,
            ), mock.patch(
                "app.crewai_self_upgrade._bug_scan_module_chunks",
                return_value=[
                    {"module": "src/plugins", "files": [{"path": "src/plugins/init.py", "content": "print('x')"}]},
                    {"module": "src/core", "files": [{"path": "src/core/main.py", "content": "raise RuntimeError()"}]},
                ],
            ), mock.patch(
                "app.crewai_self_upgrade._structured_bug_scan_for_chunk",
                side_effect=[
                    (first_scan, {"name": "qa_bug_scan_src_plugins", "agent": crewai_self_upgrade.ROLE_TEST_MANAGER, "raw": '{"summary":"first"}'}),
                    (second_scan, {"name": "qa_bug_scan_src_core", "agent": crewai_self_upgrade.ROLE_TEST_MANAGER, "raw": '{"summary":"second"}'}),
                ],
            ) as scan_mock, mock.patch(
                "app.crewai_self_upgrade._crewai_llm",
                return_value=object(),
            ):
                plan, debug = crewai_self_upgrade.kickoff_upgrade_plan(
                    repo_context=repo_context,
                    project_id="projectmanager",
                    max_findings=3,
                    verbose=False,
                )

            self.assertEqual(scan_mock.call_count, 2)
            self.assertEqual(len(plan.findings), 1)
            self.assertEqual(plan.findings[0].title, "修复核心路径缺陷")
            task_output_names = [str(item.get("name") or "") for item in (debug.get("task_outputs") or [])]
            self.assertIn("qa_bug_scan_src_plugins", task_output_names)
            self.assertIn("qa_bug_scan_src_core", task_output_names)
            self.assertIn("structured_bug_plan", task_output_names)

    def test_kickoff_upgrade_plan_bug_only_stops_after_first_verified_bug(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / ".git").mkdir()
            repo_context = {
                **self._repo_context(repo, head_commit="abc123"),
                "repository_inspection": {"tracked_file_count": 6, "category_counts": {"source": 3}, "baseline_checks": []},
            }
            first_scan = crewai_self_upgrade.StructuredBugScanResult(
                summary="src/plugins 模块发现可证明缺陷。",
                findings=[
                    crewai_self_upgrade.StructuredBugCandidate(
                        title="修复插件初始化缺陷",
                        summary="插件初始化路径在当前输入下会稳定失败。",
                        module="Runtime",
                        rationale="模块全文阅读和基线检查均指向初始化异常。",
                        files=["src/plugins/init.py"],
                        tests=["python -m unittest tests.test_plugins"],
                        acceptance=["插件初始化后不再报错"],
                        reproduction_steps=["运行插件初始化入口"],
                        test_case_files=["tests/test_plugins.py"],
                        verification_steps=["重新运行插件初始化测试"],
                    )
                ],
                ci_actions=["为插件初始化回归补充稳定测试命令"],
                notes=["first module completed"],
            )
            with mock.patch(
                "app.crewai_self_upgrade._enabled_planning_workflow_ids",
                return_value=[crewai_self_upgrade.crewai_role_registry.WORKFLOW_BUG_FIX],
            ), mock.patch(
                "app.crewai_self_upgrade._use_bug_only_fast_path",
                return_value=True,
            ), mock.patch(
                "app.crewai_self_upgrade._bug_scan_module_chunks",
                return_value=[
                    {"module": "src/plugins", "files": [{"path": "src/plugins/init.py", "content": "print('x')"}]},
                    {"module": "src/core", "files": [{"path": "src/core/main.py", "content": "raise RuntimeError()"}]},
                ],
            ), mock.patch(
                "app.crewai_self_upgrade._structured_bug_scan_for_chunk",
                side_effect=[
                    (first_scan, {"name": "qa_bug_scan_src_plugins", "agent": crewai_self_upgrade.ROLE_TEST_MANAGER, "raw": '{"summary":"first"}'}),
                    AssertionError("second module should not be scanned once a bug is found"),
                ],
            ) as scan_mock, mock.patch(
                "app.crewai_self_upgrade._crewai_llm",
                return_value=object(),
            ):
                plan, debug = crewai_self_upgrade.kickoff_upgrade_plan(
                    repo_context=repo_context,
                    project_id="projectmanager",
                    max_findings=3,
                    verbose=False,
                )

            self.assertEqual(scan_mock.call_count, 1)
            self.assertEqual(len(plan.findings), 1)
            self.assertEqual(plan.findings[0].title, "修复插件初始化缺陷")
            raw = str(debug.get("raw") or "")
            self.assertIn("已发现可证明 bug", raw)

    def test_crewai_llm_matches_codex_oauth_demo_defaults(self):
        captured: dict[str, object] = {}

        class FakeLLM:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        fake_crewai_pkg = ModuleType("crewai")
        fake_crewai_llm = ModuleType("crewai.llm")
        fake_crewai_llm.LLM = FakeLLM

        with mock.patch("app.crewai_self_upgrade.crewai_runtime.require_crewai_importable", return_value={"importable": True}), mock.patch(
            "app.crewai_self_upgrade.codex_llm.codex_login_status",
            return_value=(True, "Logged in using ChatGPT"),
        ), mock.patch.dict(
            sys.modules,
            {"crewai": fake_crewai_pkg, "crewai.llm": fake_crewai_llm},
            clear=False,
        ), mock.patch.dict(
            os.environ,
            {
                "OPENAI_API_KEY": "sk-test",
                "TEAMOS_LLM_BASE_URL": "https://api.openai.com/v1",
            },
            clear=True,
        ):
            crewai_self_upgrade._crewai_llm()

        self.assertEqual(captured["model"], "openai-codex/gpt-5.4")
        self.assertEqual(captured["api"], "responses")
        self.assertEqual(captured["is_litellm"], False)
        self.assertEqual(captured["max_tokens"], 4000)
        self.assertEqual(captured["reasoning_effort"], "xhigh")
        self.assertNotIn("api_key", captured)
        self.assertNotIn("base_url", captured)

    def test_crewai_llm_adds_chatgpt_to_no_proxy_for_codex_oauth(self):
        captured: dict[str, object] = {}

        class FakeLLM:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        fake_crewai_pkg = ModuleType("crewai")
        fake_crewai_llm = ModuleType("crewai.llm")
        fake_crewai_llm.LLM = FakeLLM

        with mock.patch("app.crewai_self_upgrade.crewai_runtime.require_crewai_importable", return_value={"importable": True}), mock.patch(
            "app.crewai_self_upgrade.codex_llm.codex_login_status",
            return_value=(True, "Logged in using ChatGPT"),
        ), mock.patch.dict(
            sys.modules,
            {"crewai": fake_crewai_pkg, "crewai.llm": fake_crewai_llm},
            clear=False,
        ), mock.patch.dict(
            os.environ,
            {
                "HTTP_PROXY": "http://host.docker.internal:1082",
                "HTTPS_PROXY": "http://host.docker.internal:1082",
                "NO_PROXY": "localhost,127.0.0.1,postgres",
            },
            clear=True,
        ):
            crewai_self_upgrade._crewai_llm()
            no_proxy = str(os.getenv("NO_PROXY") or "")

        self.assertIn("chatgpt.com", no_proxy)
        self.assertIn(".chatgpt.com", no_proxy)
        self.assertIn("api.openai.com", no_proxy)

    def test_zh_localization_requires_codex_cli(self):
        with mock.patch.dict(os.environ, {"TEAMOS_REPO_IMPROVEMENT_LOCALIZE_ZH": "1"}, clear=False), mock.patch(
            "app.crewai_self_upgrade.shutil.which",
            return_value=None,
        ):
            self.assertFalse(crewai_self_upgrade._zh_localization_enabled())

    def test_run_self_upgrade_dry_run_skips_materialized_records(self):
        db = _FakeDB()
        plan = crewai_self_upgrade.UpgradePlan(
            summary="planned",
            findings=[
                crewai_self_upgrade.UpgradeFinding(
                    kind="CI",
                    title="Add runtime CI",
                    summary="Missing automated tests in GitHub Actions.",
                    workstream_id="general",
                    tests=["python -m unittest tests.test_crewai_orchestrator"],
                )
            ],
            ci_actions=["Add GitHub Actions workflow"],
        )

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "team-os-runtime"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / ".git").mkdir()
            (repo / "README.md").write_text("# Demo\n", encoding="utf-8")

            spec = SimpleNamespace(
                project_id="teamos",
                workstream_id="general",
                repo_path=str(repo),
                repo_locator="foo/bar",
                force=True,
                dry_run=True,
                trigger="test",
                task_id="",
            )

            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False):
                with mock.patch(
                    "app.crewai_self_upgrade.kickoff_upgrade_plan",
                    return_value=(plan, {"task_outputs": [], "token_usage": {}}),
                ), mock.patch(
                    "app.crewai_self_upgrade.GitHubProjectsPanelSync.sync",
                    return_value={"ok": True, "dry_run": True, "stats": {"created": 1}},
                ), mock.patch(
                    "app.crewai_self_upgrade._ensure_issue_record",
                    side_effect=AssertionError("dry_run should not create issues"),
                ), mock.patch(
                    "app.crewai_self_upgrade._ensure_task_record",
                    side_effect=AssertionError("dry_run should not create tasks"),
                ):
                    out = crewai_self_upgrade.run_self_upgrade(
                        db=db,
                        spec=spec,
                        actor="test",
                        run_id="run-1",
                        crewai_info={"importable": True},
                    )

                self.assertTrue(out["ok"])
                self.assertTrue(out["dry_run"])
                self.assertEqual(out["records"][0]["workflow_id"], "bug-fix")
                self.assertEqual(out["records"][0]["task_id"], "")
                self.assertEqual(out["records"][0]["issue_url"], "")
                state = crewai_self_upgrade._read_state(out["target_id"])
                self.assertEqual((state.get("last_run") or {}).get("status"), "DONE")

    def test_run_self_upgrade_keeps_requested_project_id_without_panel_mapping(self):
        db = _FakeDB()
        empty_plan = crewai_self_upgrade.UpgradePlan(summary="planned", findings=[])

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "team-os-runtime"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / ".git").mkdir()
            (repo / "README.md").write_text("# Demo\n", encoding="utf-8")

            spec = SimpleNamespace(
                project_id="projectmanager",
                workstream_id="general",
                repo_path=str(repo),
                repo_locator="wangguanran/ProjectManager",
                force=True,
                dry_run=True,
                trigger="test",
                task_id="",
            )

            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False), mock.patch(
                "app.crewai_self_upgrade.collect_repo_context",
                return_value={
                    **self._repo_context(repo, head_commit="abc123"),
                    "repo_locator": "wangguanran/ProjectManager",
                },
            ), mock.patch(
                "app.crewai_self_upgrade.kickoff_upgrade_plan",
                return_value=(empty_plan, {"task_outputs": [], "token_usage": {}}),
            ), mock.patch(
                "app.crewai_self_upgrade.GitHubProjectsPanelSync.sync",
                return_value={"ok": False, "dry_run": True, "error": "not configured"},
            ):
                out = crewai_self_upgrade.run_self_upgrade(
                    db=db,
                    spec=spec,
                    actor="test",
                    run_id="run-projectmanager",
                    crewai_info={"importable": True},
                )

            self.assertTrue(out["ok"])
            started = next(e for e in db.events if e.get("event_type") == "REPO_IMPROVEMENT_STARTED")
            self.assertEqual(started.get("project_id"), "projectmanager")
            reports = improvement_store.list_reports(project_id="projectmanager")
            self.assertTrue(reports)
            self.assertEqual(str(reports[0].get("run_id") or ""), "run-projectmanager")

    def test_decide_proposal_updates_version_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False):
                improvement_store.upsert_proposal(
                    {
                        "proposal_id": "su-feature-demo",
                        "project_id": "teamos",
                        "target_id": "teamos",
                        "lane": "feature",
                        "title": "Improve onboarding",
                        "summary": "Ship a new onboarding dashboard.",
                        "status": "PENDING_CONFIRMATION",
                        "current_version": "1.2.3",
                        "version_bump": "minor",
                        "target_version": "1.3.0",
                        "baseline_action": "new_baseline",
                    }
                )
                out = crewai_self_upgrade.decide_proposal(
                    proposal_id="su-feature-demo",
                    action="approve",
                    version_bump="major",
                )

            self.assertEqual(out["status"], "APPROVED")
            self.assertEqual(out["version_bump"], "major")
            self.assertEqual(out["target_version"], "2.0.0")
            self.assertEqual(out["baseline_action"], "new_baseline")

    def test_normalize_worktree_hint_rehomes_relative_and_legacy_paths(self):
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            repo_root = Path(td) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False):
                relative = crewai_self_upgrade._normalize_worktree_hint(
                    repo_root=repo_root,
                    lane="bug",
                    title="Startup fix",
                    raw_hint="upgrade/bug-entrypoint-wiring",
                )
                legacy = crewai_self_upgrade._normalize_worktree_hint(
                    repo_root=repo_root,
                    lane="bug",
                    title="Startup fix",
                    raw_hint=str(repo_root / "scaffolds" / "runtime" / "orchestrator" / "wt-bug-startup-fix"),
                )

            self.assertEqual(
                relative,
                str((runtime_root / "workspace" / "worktrees" / "upgrade" / "bug-entrypoint-wiring").resolve()),
            )
            self.assertEqual(
                legacy,
                str((runtime_root / "workspace" / "worktrees" / "wt-bug-startup-fix").resolve()),
            )

    def test_run_self_upgrade_feature_finding_creates_pending_proposal(self):
        db = _FakeDB()
        plan = crewai_self_upgrade.UpgradePlan(
            summary="feature pending",
            findings=[
                crewai_self_upgrade.UpgradeFinding(
                    kind="FEATURE",
                    lane="feature",
                    title="Add team dashboard",
                    summary="Introduce a dashboard for team health.",
                    workstream_id="general",
                    version_bump="minor",
                    target_version="0.2.0",
                    requires_user_confirmation=True,
                    cooldown_hours=1,
                    work_items=[
                        crewai_self_upgrade.UpgradeWorkItem(
                            title="Build dashboard UI",
                            summary="Implement the first dashboard screen.",
                            owner_role="Feature-Coding-Agent",
                            review_role="Review-Agent",
                            qa_role="QA-Agent",
                            workstream_id="general",
                            allowed_paths=["src/dashboard.py"],
                            tests=["python -m unittest tests.test_dashboard"],
                            acceptance=["Dashboard renders key team metrics"],
                            worktree_hint="/tmp/worktrees/dashboard",
                        )
                    ],
                )
            ],
        )

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "team-os-runtime"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / ".git").mkdir()
            (repo / "README.md").write_text("# Demo\n", encoding="utf-8")

            spec = SimpleNamespace(
                project_id="teamos",
                workstream_id="general",
                repo_path=str(repo),
                repo_locator="foo/bar",
                force=True,
                dry_run=False,
                trigger="test",
                task_id="",
            )

            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False):
                with mock.patch(
                    "app.crewai_self_upgrade.kickoff_upgrade_plan",
                    return_value=(plan, {"task_outputs": [], "token_usage": {}}),
                ), mock.patch(
                    "app.crewai_self_upgrade._ensure_proposal_discussion_issue",
                    side_effect=lambda proposal: {**proposal, "discussion_issue_number": 12, "discussion_issue_url": "https://example.com/issues/12"},
                ), mock.patch(
                    "app.crewai_self_upgrade.GitHubProjectsPanelSync.sync",
                    return_value={"ok": True, "dry_run": False, "stats": {"updated": 0}},
                ), mock.patch(
                    "app.crewai_self_upgrade._ensure_issue_record",
                    side_effect=AssertionError("feature proposals must not materialize before approval"),
                ), mock.patch(
                    "app.crewai_self_upgrade._ensure_task_record",
                    side_effect=AssertionError("feature proposals must not materialize before approval"),
                ):
                    out = crewai_self_upgrade.run_self_upgrade(
                        db=db,
                        spec=spec,
                        actor="test",
                        run_id="run-feature-pending",
                        crewai_info={"importable": True},
                    )

                self.assertTrue(out["ok"])
                self.assertEqual(out["records"], [])
                self.assertEqual(len(out["pending_proposals"]), 1)
                self.assertEqual(out["pending_proposals"][0]["workflow_id"], "feature-improvement")
                self.assertEqual(out["pending_proposals"][0]["status"], "PENDING_CONFIRMATION")
                self.assertEqual(out["pending_proposals"][0]["discussion_issue_url"], "https://example.com/issues/12")
                proposals = improvement_store.list_proposals(target_id=out["target_id"])
                self.assertEqual(len(proposals), 1)
                self.assertEqual(proposals[0].get("workflow_id"), "feature-improvement")

    def test_coerce_plan_limits_feature_findings_to_workflow_cap(self):
        feature_findings = [
            crewai_self_upgrade.UpgradeFinding(
                kind="FEATURE",
                lane="feature",
                title=f"Feature {idx}",
                summary=f"Summary {idx}",
                workstream_id="general",
                version_bump="minor",
                target_version="0.2.0",
                requires_user_confirmation=True,
                work_items=[
                    crewai_self_upgrade.UpgradeWorkItem(
                        title=f"Feature work item {idx}",
                        summary="Implement feature candidate",
                        owner_role="Feature-Coding-Agent",
                        review_role="Review-Agent",
                        qa_role="QA-Agent",
                        workstream_id="general",
                        allowed_paths=[f"src/feature_{idx}.py"],
                        tests=[f"python -m unittest tests.test_feature_{idx}"],
                        acceptance=[f"Feature {idx} is available"],
                        worktree_hint=f"/tmp/worktrees/feature-{idx}",
                    )
                ],
            )
            for idx in range(6)
        ]
        bug_finding = crewai_self_upgrade.UpgradeFinding(
            kind="BUG",
            lane="bug",
            title="Bug 1",
            summary="Fix bug",
            workstream_id="general",
            version_bump="patch",
            target_version="0.1.1",
            work_items=[
                crewai_self_upgrade.UpgradeWorkItem(
                    title="Bug work item",
                    summary="Fix bug",
                    owner_role="Bugfix-Coding-Agent",
                    review_role="Review-Agent",
                    qa_role="QA-Agent",
                    workstream_id="general",
                    allowed_paths=["src/bug.py"],
                    tests=["python -m unittest tests.test_bug"],
                    acceptance=["Bug fixed"],
                    worktree_hint="/tmp/worktrees/bug-1",
                )
            ],
        )
        plan = crewai_self_upgrade.UpgradePlan(summary="capped", findings=[*feature_findings, bug_finding])

        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            normalized = crewai_self_upgrade._coerce_plan(
                SimpleNamespace(to_dict=lambda: plan.model_dump()),
                max_findings=10,
                repo_root=repo_root,
                current_version="0.1.0",
                project_id="teamos",
            )

        feature_titles = [item.title for item in normalized.findings if item.lane == "feature"]
        bug_titles = [item.title for item in normalized.findings if item.lane == "bug"]
        self.assertEqual(len(feature_titles), 5)
        self.assertEqual(bug_titles, ["Bug 1"])

    def test_run_self_upgrade_approved_feature_materializes_after_cooldown(self):
        db = _FakeDB()
        finding = crewai_self_upgrade.UpgradeFinding(
            kind="FEATURE",
            lane="feature",
            title="Add team dashboard",
            summary="Introduce a dashboard for team health.",
            workstream_id="general",
            version_bump="minor",
            target_version="0.2.0",
            requires_user_confirmation=True,
            cooldown_hours=1,
            work_items=[
                crewai_self_upgrade.UpgradeWorkItem(
                    title="Build dashboard UI",
                    summary="Implement the first dashboard screen.",
                    owner_role="Feature-Coding-Agent",
                    review_role="Review-Agent",
                    qa_role="QA-Agent",
                    workstream_id="general",
                    allowed_paths=["src/dashboard.py"],
                    tests=["python -m unittest tests.test_dashboard"],
                    acceptance=["Dashboard renders key team metrics"],
                    worktree_hint="/tmp/worktrees/dashboard",
                )
            ],
        )
        plan = crewai_self_upgrade.UpgradePlan(summary="feature approved", findings=[finding])

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "team-os-runtime"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / ".git").mkdir()
            (repo / "README.md").write_text("# Demo\n", encoding="utf-8")

            spec = SimpleNamespace(
                project_id="teamos",
                workstream_id="general",
                repo_path=str(repo),
                repo_locator="foo/bar",
                force=True,
                dry_run=False,
                trigger="test",
                task_id="",
            )

            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False):
                proposal = crewai_self_upgrade._upsert_proposal(
                    target_id="teamos",
                    repo_root=repo,
                    repo_locator="foo/bar",
                    project_id="teamos",
                    finding=finding,
                    current_version="0.1.0",
                )
                crewai_self_upgrade.decide_proposal(proposal_id=str(proposal["proposal_id"]), action="approve")
                current = improvement_store.get_proposal(str(proposal["proposal_id"]))
                assert current is not None
                current["cooldown_until"] = "2026-01-01T00:00:00Z"
                improvement_store.upsert_proposal(current)

                with mock.patch(
                    "app.crewai_self_upgrade.kickoff_upgrade_plan",
                    return_value=(plan, {"task_outputs": [], "token_usage": {}}),
                ), mock.patch(
                    "app.crewai_self_upgrade.GitHubProjectsPanelSync.sync",
                    return_value={"ok": True, "dry_run": False, "stats": {"updated": 1}},
                ), mock.patch(
                    "app.crewai_self_upgrade._ensure_issue_record",
                    return_value=crewai_self_upgrade._IssueRecord(title="issue", url="https://example.com/1"),
                ), mock.patch(
                    "app.crewai_self_upgrade._ensure_task_record",
                    return_value={"task_id": "TEAMOS-1234", "ledger_path": "/tmp/task.yaml"},
                ):
                    out = crewai_self_upgrade.run_self_upgrade(
                        db=db,
                        spec=spec,
                        actor="test",
                        run_id="run-feature-approved",
                        crewai_info={"importable": True},
                    )

                updated = improvement_store.get_proposal(str(proposal["proposal_id"]))

            self.assertTrue(out["ok"])
            self.assertEqual(len(out["records"]), 1)
            self.assertEqual(out["records"][0]["task_id"], "TEAMOS-1234")
            self.assertEqual((updated or {})["status"], "MATERIALIZED")

    def test_run_self_upgrade_skips_disabled_feature_workflow(self):
        db = _FakeDB()
        plan = crewai_self_upgrade.UpgradePlan(
            summary="feature disabled",
            findings=[
                crewai_self_upgrade.UpgradeFinding(
                    kind="FEATURE",
                    lane="feature",
                    title="Add team dashboard",
                    summary="Introduce a dashboard for team health.",
                    workstream_id="general",
                    version_bump="minor",
                    target_version="0.2.0",
                    requires_user_confirmation=True,
                )
            ],
        )

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "team-os-runtime"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / ".git").mkdir()
            (repo / "README.md").write_text("# Demo\n", encoding="utf-8")

            spec = SimpleNamespace(
                project_id="teamos",
                workstream_id="general",
                repo_path=str(repo),
                repo_locator="foo/bar",
                force=True,
                dry_run=False,
                trigger="test",
                task_id="",
            )

            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False):
                with mock.patch(
                    "app.crewai_self_upgrade.kickoff_upgrade_plan",
                    return_value=(plan, {"task_outputs": [], "token_usage": {}}),
                ), mock.patch(
                    "app.crewai_self_upgrade.GitHubProjectsPanelSync.sync",
                    return_value={"ok": True, "dry_run": False, "stats": {"updated": 0}},
                ), mock.patch(
                    "app.crewai_self_upgrade.crewai_workflow_registry.project_config_store.load_project_config",
                    return_value={
                        "repo_improvement": {
                            "workflow_settings": {
                                "feature-improvement": {
                                    "enabled": False,
                                    "disabled_reason": "disabled_for_repo",
                                }
                            }
                        }
                    },
                ), mock.patch(
                    "app.crewai_self_upgrade._ensure_proposal_discussion_issue",
                    side_effect=AssertionError("disabled feature workflow must not create proposal discussion"),
                ), mock.patch(
                    "app.crewai_self_upgrade._ensure_issue_record",
                    side_effect=AssertionError("disabled feature workflow must not create issues"),
                ), mock.patch(
                    "app.crewai_self_upgrade._ensure_task_record",
                    side_effect=AssertionError("disabled feature workflow must not create tasks"),
                ):
                    out = crewai_self_upgrade.run_self_upgrade(
                        db=db,
                        spec=spec,
                        actor="test",
                        run_id="run-feature-disabled",
                        crewai_info={"importable": True},
                    )

            self.assertTrue(out["ok"])
            self.assertEqual(out["records"], [])
            self.assertEqual(out["pending_proposals"], [])
            self.assertEqual(improvement_store.list_proposals(target_id=out["target_id"]), [])
            skipped = [event for event in db.events if event.get("event_type") == "REPO_IMPROVEMENT_FINDING_SKIPPED"]
            self.assertEqual(len(skipped), 1)
            self.assertEqual(skipped[0]["payload"]["workflow_id"], "feature-improvement")
            self.assertEqual(skipped[0]["payload"]["reason"], "disabled_for_repo")

    def test_planning_role_ids_only_include_bug_chain_when_bug_workflow_is_exclusive(self):
        with mock.patch(
            "app.crewai_self_upgrade.crewai_workflow_registry.project_config_store.load_project_config",
            return_value={
                "repo_improvement": {
                    "workflow_settings": {
                        "feature-improvement": {"enabled": False},
                        "quality-improvement": {"enabled": False},
                        "process-improvement": {"enabled": False},
                        "bug-fix": {"enabled": True},
                    }
                }
            },
        ):
            roles = crewai_self_upgrade._planning_role_ids(project_id="teamos")

        self.assertEqual(
            roles,
            {
                crewai_self_upgrade.ROLE_TEST_MANAGER,
                crewai_self_upgrade.ROLE_ISSUE_DRAFTER,
                crewai_self_upgrade.ROLE_PLAN_REVIEW_AGENT,
                crewai_self_upgrade.ROLE_PLAN_QA_AGENT,
            },
        )

    def test_run_self_upgrade_skips_when_all_workflows_disabled(self):
        db = _FakeDB()

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "team-os-runtime"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / ".git").mkdir()
            (repo / "README.md").write_text("# Demo\n", encoding="utf-8")

            spec = self._make_self_upgrade_spec(repo, force=True, dry_run=True)

            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False):
                with mock.patch(
                    "app.crewai_self_upgrade.crewai_workflow_registry.project_config_store.load_project_config",
                    return_value={
                        "repo_improvement": {
                            "workflow_settings": {
                                "feature-improvement": {"enabled": False},
                                "quality-improvement": {"enabled": False},
                                "process-improvement": {"enabled": False},
                                "bug-fix": {"enabled": False},
                            }
                        }
                    },
                ), mock.patch(
                    "app.crewai_self_upgrade.kickoff_upgrade_plan",
                    side_effect=AssertionError("disabled workflows should skip before planning crew kickoff"),
                ):
                    out = crewai_self_upgrade.run_self_upgrade(
                        db=db,
                        spec=spec,
                        actor="test",
                        run_id="run-no-workflows",
                        crewai_info={"importable": True},
                    )

            self.assertTrue(out["ok"])
            self.assertTrue(out["skipped"])
            self.assertEqual(out["reason"], "no_enabled_workflows")
            skipped = [event for event in db.events if event.get("event_type") == "REPO_IMPROVEMENT_SKIPPED"]
            self.assertEqual(len(skipped), 1)
            self.assertEqual(skipped[0]["payload"]["reason"], "no_enabled_workflows")

    def test_run_repo_improvement_skips_bug_workflow_outside_active_window(self):
        db = _FakeDB()
        plan = self._bug_plan(title="Fix runtime bug outside window")

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "team-os-runtime"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / ".git").mkdir()
            (repo / "README.md").write_text("# Demo\n", encoding="utf-8")

            spec = self._make_self_upgrade_spec(repo, force=False, dry_run=True)

            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False):
                with mock.patch(
                    "app.crewai_self_upgrade.kickoff_upgrade_plan",
                    return_value=(plan, {"task_outputs": [], "token_usage": {}}),
                ), mock.patch(
                    "app.crewai_self_upgrade.GitHubProjectsPanelSync.sync",
                    return_value={"ok": True, "dry_run": True, "stats": {"updated": 0}},
                ), mock.patch(
                    "app.crewai_self_upgrade.crewai_workflow_registry.project_config_store.load_project_config",
                    return_value={
                        "repo_improvement": {
                            "workflow_settings": {
                                "bug-fix": {
                                    "active_window_start_hour": 9,
                                    "active_window_end_hour": 18,
                                }
                            }
                        }
                    },
                ), mock.patch(
                    "app.crewai_self_upgrade.crewai_workflow_registry._workflow_now_local",
                    return_value=dt.datetime(2026, 3, 11, 20, 0, tzinfo=dt.timezone.utc),
                ), mock.patch(
                    "app.crewai_self_upgrade._ensure_issue_record",
                    side_effect=AssertionError("outside active window must not materialize issues"),
                ), mock.patch(
                    "app.crewai_self_upgrade._ensure_task_record",
                    side_effect=AssertionError("outside active window must not materialize tasks"),
                ):
                    out = crewai_self_upgrade.run_self_upgrade(
                        db=db,
                        spec=spec,
                        actor="test",
                        run_id="run-bug-window-closed",
                        crewai_info={"importable": True},
                    )

            self.assertTrue(out["ok"])
            self.assertEqual(out["records"], [])
            skipped = [event for event in db.events if event.get("event_type") == "REPO_IMPROVEMENT_FINDING_SKIPPED"]
            self.assertEqual(len(skipped), 1)
            self.assertEqual(skipped[0]["payload"]["workflow_id"], "bug-fix")
            self.assertEqual(skipped[0]["payload"]["reason"], "outside_active_window")

    def test_reconcile_feature_discussions_approves_from_issue_comment(self):
        db = _FakeDB()
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False):
                improvement_store.upsert_proposal(
                    {
                        "proposal_id": "su-feature-demo",
                        "project_id": "teamos",
                        "target_id": "teamos",
                        "lane": "feature",
                        "title": "Improve onboarding",
                        "summary": "Ship a new onboarding dashboard.",
                        "status": "PENDING_CONFIRMATION",
                        "current_version": "1.2.3",
                        "version_bump": "minor",
                        "target_version": "1.3.0",
                        "baseline_action": "new_baseline",
                        "repo_locator": "foo/bar",
                        "workstream_id": "general",
                        "discussion_issue_number": 12,
                        "discussion_issue_url": "https://example.com/issues/12",
                        "discussion_last_comment_id": 0,
                    }
                )
                comment = SimpleNamespace(
                    id=101,
                    body="/approve",
                    user_login="wangguanran",
                    created_at="2026-03-06T06:35:00Z",
                    updated_at="2026-03-06T06:35:00Z",
                )
                with mock.patch(
                    "app.crewai_self_upgrade.list_issue_comments",
                    return_value=[comment],
                ), mock.patch(
                    "app.crewai_self_upgrade.update_issue",
                    return_value=SimpleNamespace(number=12, url="https://example.com/issues/12", title="t", body="b"),
                ), mock.patch(
                    "app.crewai_self_upgrade.upsert_comment_with_marker",
                    return_value=SimpleNamespace(id=201, url="https://example.com/issues/12#issuecomment-201", body="ok"),
                ), mock.patch(
                    "app.crewai_self_upgrade.kickoff_proposal_discussion",
                    return_value=crewai_self_upgrade.ProposalDiscussionResponse(reply_body="approved", action="pending"),
                ):
                    out = crewai_self_upgrade.reconcile_feature_discussions(db=db, actor="test")

                updated = improvement_store.get_proposal("su-feature-demo")

            self.assertEqual(out["updated"], 1)
            self.assertEqual(out["replied"], 1)
            self.assertEqual((updated or {})["status"], "APPROVED")
            self.assertEqual((updated or {})["discussion_last_comment_id"], 101)
            self.assertFalse((updated or {})["awaiting_user_reply"])

    def test_proposal_issue_template_is_chinese(self):
        doc = {
            "proposal_id": "su-feature-demo",
            "repo_root": "/tmp/team-os",
            "repo_locator": "foo/bar",
            "module": "Runtime",
            "status": "PENDING_CONFIRMATION",
            "version_bump": "minor",
            "target_version": "0.2.0",
            "cooldown_until": "2026-03-07T02:00:00Z",
            "title": "运行时启动预检",
            "summary": "在开发前先完成运行时启动预检。",
            "rationale": "这样可以减少启动回归。",
            "work_items": [
                {
                    "title": "补齐启动预检命令",
                    "owner_role": crewai_self_upgrade.ROLE_FEATURE_CODING_AGENT,
                }
            ],
        }

        title = crewai_self_upgrade._proposal_issue_title(doc)
        body = crewai_self_upgrade._proposal_issue_body(doc)
        labels = crewai_self_upgrade._proposal_issue_labels(doc)

        self.assertEqual(title, "[Feature][Runtime] 运行时启动预检")
        self.assertIn("# 改进提案讨论", body)
        self.assertIn("## 如何回复", body)
        self.assertIn("功能编码 Agent", body)
        self.assertIn("- Module: Runtime", body)
        self.assertEqual(
            labels,
            [
                "module:runtime",
                "proposal:pending-confirmation",
                "source:repo-improvement",
                "stage:proposal",
                "teamos",
                "type:feature",
                "version:minor",
            ],
        )

    def test_quality_lane_uses_quality_titles_and_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False):
                finding = crewai_self_upgrade.UpgradeFinding(
                    kind="CODE_QUALITY",
                    lane="quality",
                    title="删除未引用的旧启动适配层",
                    summary="清理当前已经没有调用路径的旧适配文件，并抽取公共启动检查逻辑。",
                    module="Runtime",
                    rationale="旧适配层已经和当前入口脱节，继续保留会增加维护成本。",
                    impact="MED",
                    workstream_id="general",
                    files=["scaffolds/runtime/orchestrator/app/main.py", "scaffolds/runtime/orchestrator/app/legacy_startup.py"],
                    version_bump="none",
                    target_version="0.1.0",
                )
                normalized = crewai_self_upgrade._coerce_plan(
                    SimpleNamespace(to_dict=lambda: crewai_self_upgrade.UpgradePlan(summary="quality", findings=[finding]).model_dump()),
                    max_findings=3,
                    repo_root=Path("/tmp/team-os"),
                    current_version="0.1.0",
                )
                proposal = crewai_self_upgrade._upsert_proposal(
                    target_id="teamos",
                    repo_root=Path("/tmp/team-os"),
                    repo_locator="foo/bar",
                    project_id="teamos",
                    finding=normalized.findings[0],
                    current_version="0.1.0",
                )

            self.assertEqual(normalized.findings[0].kind, "CODE_QUALITY")
            self.assertEqual(normalized.findings[0].lane, "quality")
            self.assertTrue(normalized.findings[0].requires_user_confirmation)
            self.assertEqual(normalized.findings[0].version_bump, "none")
            self.assertEqual(normalized.findings[0].work_items[0].owner_role, crewai_self_upgrade.ROLE_CODE_QUALITY_AGENT)
            self.assertEqual(crewai_self_upgrade._proposal_issue_title(proposal), "[Quality][Runtime] 删除未引用的旧启动适配层")
            self.assertIn("type:quality", crewai_self_upgrade._proposal_issue_labels(proposal))

    def test_quality_test_gap_metadata_survives_plan_normalization(self):
        finding = crewai_self_upgrade.UpgradeFinding(
            kind="CODE_QUALITY",
            lane="quality",
            title="补齐 runtime 健康检查黑盒覆盖",
            summary="当前 /healthz 用户路径缺少集成级回归覆盖。",
            module="Runtime",
            rationale="没有黑盒覆盖时，启动与健康检查回归容易漏掉。",
            impact="MED",
            workstream_id="general",
            files=["scaffolds/runtime/orchestrator/app/main.py", "tests/test_runtime_health.py"],
            tests=["python -m unittest tests.test_runtime_health"],
            acceptance=["新增集成测试能覆盖 /healthz 行为"],
            test_gap_type="blackbox",
            target_paths=["scaffolds/runtime/orchestrator/app/main.py"],
            missing_paths=["/healthz startup path"],
            suggested_test_files=["tests/test_runtime_health.py"],
            why_not_covered="当前只有单元级启动测试，没有面向健康检查行为的黑盒回归。",
            version_bump="none",
            target_version="0.1.0",
            work_items=[
                crewai_self_upgrade.UpgradeWorkItem(
                    title="补齐 /healthz 黑盒回归",
                    summary="新增端到端测试覆盖运行时健康检查。",
                    owner_role=crewai_self_upgrade.ROLE_CODE_QUALITY_AGENT,
                    review_role=crewai_self_upgrade.ROLE_REVIEW_AGENT,
                    qa_role=crewai_self_upgrade.ROLE_QA_AGENT,
                    workstream_id="general",
                    allowed_paths=["tests/test_runtime_health.py", "scaffolds/runtime/orchestrator/app/main.py"],
                    tests=["python -m unittest tests.test_runtime_health"],
                    acceptance=["新增集成测试能覆盖 /healthz 行为"],
                    test_gap_type="blackbox",
                    target_paths=["scaffolds/runtime/orchestrator/app/main.py"],
                    missing_paths=["/healthz startup path"],
                    suggested_test_files=["tests/test_runtime_health.py"],
                    why_not_covered="当前只有单元级启动测试，没有面向健康检查行为的黑盒回归。",
                    worktree_hint="/tmp/worktrees/runtime-health-gap",
                    module="Runtime",
                )
            ],
        )

        with tempfile.TemporaryDirectory() as td:
            repo_root = Path(td) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            normalized = crewai_self_upgrade._coerce_plan(
                SimpleNamespace(to_dict=lambda: crewai_self_upgrade.UpgradePlan(summary="quality", findings=[finding]).model_dump()),
                max_findings=3,
                repo_root=repo_root,
                current_version="0.1.0",
            )

        normalized_finding = normalized.findings[0]
        normalized_item = normalized_finding.work_items[0]
        self.assertEqual(normalized_finding.test_gap_type, "blackbox")
        self.assertEqual(normalized_finding.target_paths, ["scaffolds/runtime/orchestrator/app/main.py"])
        self.assertEqual(normalized_finding.missing_paths, ["/healthz startup path"])
        self.assertEqual(normalized_finding.suggested_test_files, ["tests/test_runtime_health.py"])
        self.assertIn("黑盒回归", normalized_finding.why_not_covered)
        self.assertEqual(normalized_item.test_gap_type, "blackbox")
        self.assertEqual(normalized_item.suggested_test_files, ["tests/test_runtime_health.py"])
        self.assertEqual(normalized_item.owner_role, crewai_self_upgrade.ROLE_CODE_QUALITY_AGENT)

    def test_quality_test_gap_proposal_issue_includes_gap_metadata(self):
        doc = {
            "proposal_id": "su-quality-gap",
            "repo_root": "/tmp/team-os",
            "repo_locator": "foo/bar",
            "lane": "quality",
            "module": "Runtime",
            "status": "PENDING_CONFIRMATION",
            "version_bump": "none",
            "target_version": "0.1.0",
            "title": "补齐 runtime 健康检查黑盒覆盖",
            "summary": "当前 /healthz 用户路径缺少集成级回归覆盖。",
            "rationale": "没有黑盒覆盖时，启动与健康检查回归容易漏掉。",
            "test_gap_type": "blackbox",
            "target_paths": ["scaffolds/runtime/orchestrator/app/main.py"],
            "missing_paths": ["/healthz startup path"],
            "suggested_test_files": ["tests/test_runtime_health.py"],
            "why_not_covered": "当前只有单元级启动测试，没有面向健康检查行为的黑盒回归。",
            "work_items": [
                {
                    "title": "补齐 /healthz 黑盒回归",
                    "owner_role": crewai_self_upgrade.ROLE_CODE_QUALITY_AGENT,
                }
            ],
        }

        body = crewai_self_upgrade._proposal_issue_body(doc)
        labels = crewai_self_upgrade._proposal_issue_labels(doc)

        self.assertIn("## 测试缺口分析", body)
        self.assertIn("- 测试缺口类型: blackbox", body)
        self.assertIn("- 建议测试文件: tests/test_runtime_health.py", body)
        self.assertIn("未覆盖原因", body)
        self.assertIn("test-gap:blackbox", labels)

    def test_task_issue_template_is_chinese(self):
        finding = crewai_self_upgrade.UpgradeFinding(
            kind="BUG",
            lane="bug",
            title="修复启动导入回归",
            summary="修复启动路径中的导入回归。",
            module="Runtime",
            rationale="当前启动链路仍可能引用旧模块。",
            impact="HIGH",
            workstream_id="general",
            files=["scaffolds/runtime/orchestrator/app/main.py"],
            tests=["python -m unittest tests.test_crewai_runtime"],
            acceptance=["启动后 /healthz 返回 ok"],
            version_bump="patch",
            target_version="0.1.1",
        )
        item = crewai_self_upgrade.UpgradeWorkItem(
            title="清理旧导入引用",
            summary="移除旧 self_improve runner 引用。",
            owner_role=crewai_self_upgrade.ROLE_BUGFIX_CODING_AGENT,
            review_role=crewai_self_upgrade.ROLE_REVIEW_AGENT,
            qa_role=crewai_self_upgrade.ROLE_QA_AGENT,
            allowed_paths=["scaffolds/runtime/orchestrator/app/main.py"],
            tests=["python -m unittest tests.test_crewai_runtime"],
            acceptance=["启动后 /healthz 返回 ok"],
            worktree_hint="/tmp/wt-bug",
            module="Runtime",
        )

        title = crewai_self_upgrade._issue_title_for_work_item("team-os", finding, item)
        body = crewai_self_upgrade._issue_body(
            repo_root=Path("/tmp/team-os"),
            repo_locator="foo/bar",
            finding=finding,
            work_item=item,
            fingerprint="demo-fp",
            marker="<!-- teamos:self_upgrade:demo-fp-runtime-cleanup -->",
            doc={
                "self_upgrade_audit": {
                    "status": "approved",
                    "classification": "bug",
                    "closure": "ready",
                    "worth_doing": True,
                    "docs_required": True,
                    "summary": "问题闭环，可以进入开发。",
                    "feedback": [],
                },
                "documentation_policy": {
                    "required": True,
                    "status": "pending",
                    "documentation_role": crewai_self_upgrade.ROLE_DOCUMENTATION_AGENT,
                    "allowed_paths": ["README.md", "docs"],
                    "rationale": "运行时行为变更需要同步说明文档。",
                },
            },
        )

        self.assertEqual(title, "[Bug][Runtime] 清理旧导入引用")
        self.assertIn("<!-- teamos:self_upgrade:demo-fp-runtime-cleanup -->", body)
        self.assertIn("# 仓库改进任务", body)
        self.assertIn("- Module: Runtime", body)
        self.assertIn("- GitHub Milestone: v0.1.1", body)
        self.assertIn("## 范围外", body)
        self.assertIn("## 审计状态", body)
        self.assertIn("## 文档同步", body)
        self.assertIn("## 版本与里程碑", body)
        self.assertIn("问题审计 Agent", body)
        self.assertIn("文档同步 Agent", body)
        self.assertIn("里程碑经理 Agent", body)
        self.assertIn("## 执行约束", body)
        self.assertIn("缺陷修复 Agent", body)

    def test_build_milestone_doc_assigns_patch_release_line(self):
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False):
                finding = crewai_self_upgrade.UpgradeFinding(
                    kind="BUG",
                    lane="bug",
                    title="修复启动导入回归",
                    summary="修复启动路径中的导入回归。",
                    module="Runtime",
                    version_bump="patch",
                    target_version="0.1.1",
                    workstream_id="general",
                )
                item = crewai_self_upgrade.UpgradeWorkItem(
                    title="清理旧导入引用",
                    summary="移除旧 self_improve runner 引用。",
                    owner_role=crewai_self_upgrade.ROLE_BUGFIX_CODING_AGENT,
                    workstream_id="general",
                    module="Runtime",
                )
                out = crewai_self_upgrade._build_milestone_doc(
                    project_id="teamos",
                    repo_locator="foo/bar",
                    finding=finding,
                    work_item=item,
                )

        self.assertEqual(out["title"], "v0.1.1")
        self.assertEqual(out["milestone_id"], "v0-1-1")
        self.assertEqual(out["release_line"], "patch")
        self.assertEqual(out["state"], "draft")
        self.assertEqual(out["manager_role"], crewai_self_upgrade.ROLE_MILESTONE_MANAGER)

    def test_sync_milestone_from_doc_persists_runtime_milestone_state(self):
        with tempfile.TemporaryDirectory() as td:
            runtime_root = Path(td) / "team-os-runtime"
            repo_root = Path(td) / "repo"
            repo_root.mkdir(parents=True, exist_ok=True)
            task_doc = {
                "project_id": "teamos",
                "task_id": "TEAMOS-1001",
                "title": "[Bug][Runtime] 清理旧导入引用",
                "status": "todo",
                "workstream_id": "general",
                "repo": {
                    "locator": "foo/bar",
                    "workdir": str(repo_root),
                },
                "links": {
                    "issue": "https://github.com/foo/bar/issues/55",
                },
                "self_upgrade": {
                    "kind": "BUG",
                    "lane": "bug",
                    "module": "Runtime",
                    "summary": "修复启动路径中的导入回归。",
                    "rationale": "当前启动链路仍可能引用旧模块。",
                    "impact": "HIGH",
                    "files": ["scaffolds/runtime/orchestrator/app/main.py"],
                    "tests": ["python -m unittest tests.test_crewai_runtime"],
                    "acceptance": ["启动后 /healthz 返回 ok"],
                    "version_bump": "patch",
                    "target_version": "0.1.1",
                    "baseline_action": "",
                    "work_item": {
                        "title": "清理旧导入引用",
                        "summary": "移除旧 self_improve runner 引用。",
                        "owner_role": crewai_self_upgrade.ROLE_BUGFIX_CODING_AGENT,
                        "review_role": crewai_self_upgrade.ROLE_REVIEW_AGENT,
                        "qa_role": crewai_self_upgrade.ROLE_QA_AGENT,
                        "workstream_id": "general",
                        "allowed_paths": ["scaffolds/runtime/orchestrator/app/main.py"],
                        "tests": ["python -m unittest tests.test_crewai_runtime"],
                        "acceptance": ["启动后 /healthz 返回 ok"],
                        "worktree_hint": str(runtime_root / "workspace" / "worktrees" / "wt-bug-runtime"),
                        "module": "Runtime",
                    },
                },
                "orchestration": {
                    "flow": "self_upgrade",
                },
            }
            release_issue = SimpleNamespace(number=101, url="https://github.com/foo/bar/issues/101", title="[Process][Release] 跟踪 v0.1.1 版本发布")
            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False), mock.patch(
                "app.crewai_self_upgrade.ensure_milestone",
                return_value=7,
            ), mock.patch(
                "app.crewai_self_upgrade.ensure_issue",
                return_value=release_issue,
            ), mock.patch(
                "app.crewai_self_upgrade.update_issue",
                return_value=release_issue,
            ):
                out = crewai_self_upgrade.sync_milestone_from_doc(task_doc)
                milestones = plan_store.list_milestones("teamos")

        self.assertTrue(out["ok"])
        self.assertEqual(task_doc["self_upgrade_milestone"]["github_milestone_number"], 7)
        self.assertEqual(task_doc["self_upgrade_milestone"]["release_issue_number"], 101)
        self.assertEqual(task_doc["self_upgrade_milestone"]["state"], "active")
        managed = [m for m in milestones if m.milestone_id == "v0-1-1"]
        self.assertEqual(len(managed), 1)
        self.assertEqual(managed[0].title, "v0.1.1")
        self.assertEqual(managed[0].release_issue_number, 101)

    def test_task_issue_labels_include_module_stage_and_version(self):
        finding = crewai_self_upgrade.UpgradeFinding(
            kind="BUG",
            lane="bug",
            title="修复启动导入回归",
            summary="修复启动路径中的导入回归。",
            module="Runtime",
            version_bump="patch",
            target_version="0.1.1",
        )
        item = crewai_self_upgrade.UpgradeWorkItem(title="清理旧导入引用", module="Runtime")
        labels = crewai_self_upgrade._task_issue_labels(doc={"status": "todo"}, finding=finding, work_item=item)
        self.assertEqual(
            labels,
            [
                "milestone:v0-1-1",
                "module:runtime",
                "source:repo-improvement",
                "stage:queued",
                "teamos",
                "type:bug",
                "version:patch",
            ],
        )

    def test_task_issue_labels_use_merge_conflict_stage_when_delivery_hits_conflict(self):
        finding = crewai_self_upgrade.UpgradeFinding(
            kind="BUG",
            lane="bug",
            title="修复发布冲突回退",
            summary="修复 release 阶段的冲突回退链路。",
            module="Self-Upgrade",
            version_bump="patch",
            target_version="0.1.1",
        )
        item = crewai_self_upgrade.UpgradeWorkItem(title="回退到 coding 处理冲突", module="Self-Upgrade")
        labels = crewai_self_upgrade._task_issue_labels(
            doc={"status": "merge_conflict", "self_upgrade_execution": {"stage": "merge_conflict"}},
            finding=finding,
            work_item=item,
        )
        self.assertIn("stage:merge-conflict", labels)

    def test_task_issue_labels_use_needs_clarification_stage_when_audit_blocks(self):
        finding = crewai_self_upgrade.UpgradeFinding(
            kind="BUG",
            lane="bug",
            title="补齐 issue 闭环描述",
            summary="当前 issue 缺少复现步骤。",
            module="Runtime",
            version_bump="patch",
            target_version="0.1.1",
        )
        item = crewai_self_upgrade.UpgradeWorkItem(title="补齐 issue 闭环描述", module="Runtime")
        labels = crewai_self_upgrade._task_issue_labels(
            doc={"status": "needs_clarification", "self_upgrade_execution": {"stage": "needs_clarification"}},
            finding=finding,
            work_item=item,
        )
        self.assertIn("stage:needs-clarification", labels)

    def test_finding_from_task_doc_roundtrips_quality_test_gap_fields(self):
        task_doc = {
            "title": "[Quality][Runtime] 补齐 /healthz 黑盒回归",
            "status": "todo",
            "workstream_id": "general",
            "execution_policy": {
                "allowed_paths": ["tests/test_runtime_health.py", "scaffolds/runtime/orchestrator/app/main.py"],
                "review_role": crewai_self_upgrade.ROLE_REVIEW_AGENT,
                "qa_role": crewai_self_upgrade.ROLE_QA_AGENT,
            },
            "self_upgrade": {
                "kind": "CODE_QUALITY",
                "lane": "quality",
                "module": "Runtime",
                "summary": "当前 /healthz 用户路径缺少集成级回归覆盖。",
                "rationale": "没有黑盒覆盖时，启动与健康检查回归容易漏掉。",
                "impact": "MED",
                "files": ["scaffolds/runtime/orchestrator/app/main.py", "tests/test_runtime_health.py"],
                "tests": ["python -m unittest tests.test_runtime_health"],
                "acceptance": ["新增集成测试能覆盖 /healthz 行为"],
                "test_gap_type": "blackbox",
                "target_paths": ["scaffolds/runtime/orchestrator/app/main.py"],
                "missing_paths": ["/healthz startup path"],
                "suggested_test_files": ["tests/test_runtime_health.py"],
                "why_not_covered": "当前只有单元级启动测试，没有面向健康检查行为的黑盒回归。",
                "version_bump": "none",
                "target_version": "0.1.0",
                "work_item": {
                    "title": "补齐 /healthz 黑盒回归",
                    "summary": "新增端到端测试覆盖运行时健康检查。",
                    "owner_role": crewai_self_upgrade.ROLE_CODE_QUALITY_AGENT,
                    "review_role": crewai_self_upgrade.ROLE_REVIEW_AGENT,
                    "qa_role": crewai_self_upgrade.ROLE_QA_AGENT,
                    "workstream_id": "general",
                    "allowed_paths": ["tests/test_runtime_health.py", "scaffolds/runtime/orchestrator/app/main.py"],
                    "tests": ["python -m unittest tests.test_runtime_health"],
                    "acceptance": ["新增集成测试能覆盖 /healthz 行为"],
                    "test_gap_type": "blackbox",
                    "target_paths": ["scaffolds/runtime/orchestrator/app/main.py"],
                    "missing_paths": ["/healthz startup path"],
                    "suggested_test_files": ["tests/test_runtime_health.py"],
                    "why_not_covered": "当前只有单元级启动测试，没有面向健康检查行为的黑盒回归。",
                    "module": "Runtime",
                },
            },
        }

        finding, item = crewai_self_upgrade._finding_from_task_doc(task_doc)

        self.assertIsNotNone(finding)
        self.assertIsNotNone(item)
        assert finding is not None
        assert item is not None
        self.assertEqual(finding.test_gap_type, "blackbox")
        self.assertEqual(finding.target_paths, ["scaffolds/runtime/orchestrator/app/main.py"])
        self.assertEqual(finding.missing_paths, ["/healthz startup path"])
        self.assertEqual(finding.suggested_test_files, ["tests/test_runtime_health.py"])
        self.assertIn("黑盒回归", finding.why_not_covered)
        self.assertEqual(item.test_gap_type, "blackbox")
        self.assertEqual(item.suggested_test_files, ["tests/test_runtime_health.py"])

    def test_bug_lane_enters_dormant_after_three_zero_bug_runs_on_same_head(self):
        db = _FakeDB()
        empty_plan = crewai_self_upgrade.UpgradePlan(summary="stable", findings=[])

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "team-os-runtime"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / ".git").mkdir()
            (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
            spec = self._make_self_upgrade_spec(repo, force=False, dry_run=True)

            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False), mock.patch(
                "app.crewai_self_upgrade.collect_repo_context",
                return_value=self._repo_context(repo, head_commit="abc123"),
            ), mock.patch(
                "app.crewai_self_upgrade.GitHubProjectsPanelSync.sync",
                return_value={"ok": True, "dry_run": True, "stats": {"updated": 0}},
            ):
                for idx in range(3):
                    with mock.patch(
                        "app.crewai_self_upgrade.kickoff_upgrade_plan",
                        return_value=(empty_plan, {"task_outputs": [], "token_usage": {}}),
                    ):
                        out = crewai_self_upgrade.run_self_upgrade(
                            db=db,
                            spec=spec,
                            actor="test",
                            run_id=f"run-zero-{idx + 1}",
                            crewai_info={"importable": True},
                        )

                state = crewai_self_upgrade._read_state(out["target_id"])

        bug_lane = ((state.get("lane_states") or {}).get("bug") or {})
        self.assertEqual(bug_lane.get("status"), "dormant")
        self.assertEqual(bug_lane.get("zero_bug_scan_streak"), 3)
        self.assertEqual(bug_lane.get("head_commit"), "abc123")
        self.assertIn("REPO_IMPROVEMENT_BUG_LANE_DORMANT", [event.get("event_type") for event in db.events])

    def test_dormant_bug_lane_filters_bug_findings_until_head_changes(self):
        db = _FakeDB()
        empty_plan = crewai_self_upgrade.UpgradePlan(summary="stable", findings=[])
        bug_plan = self._bug_plan()
        kickoff_flags: list[bool] = []

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "team-os-runtime"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / ".git").mkdir()
            (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
            spec = self._make_self_upgrade_spec(repo, force=False, dry_run=True)
            stable_context = self._repo_context(repo, head_commit="abc123")

            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False), mock.patch(
                "app.crewai_self_upgrade.GitHubProjectsPanelSync.sync",
                return_value={"ok": True, "dry_run": True, "stats": {"updated": 0}},
            ):
                with mock.patch("app.crewai_self_upgrade.collect_repo_context", return_value=stable_context):
                    for idx in range(3):
                        with mock.patch(
                            "app.crewai_self_upgrade.kickoff_upgrade_plan",
                            return_value=(empty_plan, {"task_outputs": [], "token_usage": {}}),
                        ):
                            crewai_self_upgrade.run_self_upgrade(
                                db=db,
                                spec=spec,
                                actor="test",
                                run_id=f"run-zero-{idx + 1}",
                                crewai_info={"importable": True},
                            )

                def _dormant_kickoff(*, bug_scan_dormant: bool = False, **kwargs):
                    kickoff_flags.append(bool(bug_scan_dormant))
                    return bug_plan, {"task_outputs": [], "token_usage": {}}

                with mock.patch("app.crewai_self_upgrade.collect_repo_context", return_value=stable_context), mock.patch(
                    "app.crewai_self_upgrade.kickoff_upgrade_plan",
                    side_effect=_dormant_kickoff,
                ), mock.patch(
                    "app.crewai_self_upgrade._ensure_issue_record",
                    side_effect=AssertionError("dormant bug lane must not materialize bug records"),
                ), mock.patch(
                    "app.crewai_self_upgrade._ensure_task_record",
                    side_effect=AssertionError("dormant bug lane must not materialize bug tasks"),
                ):
                    out = crewai_self_upgrade.run_self_upgrade(
                        db=db,
                        spec=spec,
                        actor="test",
                        run_id="run-dormant-skip",
                        crewai_info={"importable": True},
                    )

                state = crewai_self_upgrade._read_state(out["target_id"])

        self.assertEqual(kickoff_flags, [True])
        self.assertEqual(out["records"], [])
        bug_lane = ((state.get("lane_states") or {}).get("bug") or {})
        self.assertEqual(bug_lane.get("status"), "dormant")
        self.assertEqual(bug_lane.get("last_bug_finding_count"), 0)
        self.assertIn("REPO_IMPROVEMENT_BUG_LANE_SKIPPED", [event.get("event_type") for event in db.events])

    def test_head_change_resumes_dormant_bug_lane_and_allows_bug_materialization(self):
        db = _FakeDB()
        empty_plan = crewai_self_upgrade.UpgradePlan(summary="stable", findings=[])
        bug_plan = self._bug_plan(title="Fix startup bug")

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "team-os-runtime"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / ".git").mkdir()
            (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
            spec = self._make_self_upgrade_spec(repo, force=False, dry_run=True)

            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False), mock.patch(
                "app.crewai_self_upgrade.GitHubProjectsPanelSync.sync",
                return_value={"ok": True, "dry_run": True, "stats": {"updated": 0}},
            ):
                with mock.patch(
                    "app.crewai_self_upgrade.collect_repo_context",
                    return_value=self._repo_context(repo, head_commit="abc123"),
                ):
                    for idx in range(3):
                        with mock.patch(
                            "app.crewai_self_upgrade.kickoff_upgrade_plan",
                            return_value=(empty_plan, {"task_outputs": [], "token_usage": {}}),
                        ):
                            crewai_self_upgrade.run_self_upgrade(
                                db=db,
                                spec=spec,
                                actor="test",
                                run_id=f"run-zero-{idx + 1}",
                                crewai_info={"importable": True},
                            )

                with mock.patch(
                    "app.crewai_self_upgrade.collect_repo_context",
                    return_value=self._repo_context(repo, head_commit="def456"),
                ), mock.patch(
                    "app.crewai_self_upgrade.kickoff_upgrade_plan",
                    return_value=(bug_plan, {"task_outputs": [], "token_usage": {}}),
                ):
                    out = crewai_self_upgrade.run_self_upgrade(
                        db=db,
                        spec=spec,
                        actor="test",
                        run_id="run-head-change",
                        crewai_info={"importable": True},
                    )

                state = crewai_self_upgrade._read_state(out["target_id"])

        self.assertEqual(len(out["records"]), 1)
        self.assertEqual(out["records"][0]["workflow_id"], "bug-fix")
        bug_lane = ((state.get("lane_states") or {}).get("bug") or {})
        self.assertEqual(bug_lane.get("status"), "active")
        self.assertEqual(bug_lane.get("zero_bug_scan_streak"), 0)
        self.assertEqual(bug_lane.get("head_commit"), "def456")
        self.assertIn("REPO_IMPROVEMENT_BUG_LANE_RESUMED", [event.get("event_type") for event in db.events])

    def test_open_bug_tasks_keep_bug_lane_active_despite_zero_bug_runs(self):
        db = _FakeDB()
        empty_plan = crewai_self_upgrade.UpgradePlan(summary="stable", findings=[])

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            runtime_root = Path(td) / "team-os-runtime"
            repo.mkdir(parents=True, exist_ok=True)
            (repo / ".git").mkdir()
            (repo / "README.md").write_text("# Demo\n", encoding="utf-8")
            spec = self._make_self_upgrade_spec(repo, force=False, dry_run=True)
            stable_context = self._repo_context(repo, head_commit="abc123")

            with mock.patch.dict(os.environ, {"TEAMOS_RUNTIME_ROOT": str(runtime_root)}, clear=False), mock.patch(
                "app.crewai_self_upgrade.collect_repo_context",
                return_value=stable_context,
            ), mock.patch(
                "app.crewai_self_upgrade.GitHubProjectsPanelSync.sync",
                return_value={"ok": True, "dry_run": True, "stats": {"updated": 0}},
            ):
                target = improvement_store.ensure_target(
                    project_id="teamos",
                    repo_path=str(repo),
                    repo_locator="foo/bar",
                )
                improvement_store.upsert_delivery_task(
                    {
                        "id": "TASK-BUG-1",
                        "project_id": "teamos",
                        "title": "Existing bug task",
                        "status": "todo",
                        "target": {"target_id": str(target.get("target_id") or "")},
                        "repo": {"locator": "foo/bar", "workdir": str(repo)},
                        "orchestration": {"flow": "self_upgrade", "finding_lane": "bug"},
                        "self_upgrade": {"lane": "bug"},
                    }
                )
                for idx in range(3):
                    with mock.patch(
                        "app.crewai_self_upgrade.kickoff_upgrade_plan",
                        return_value=(empty_plan, {"task_outputs": [], "token_usage": {}}),
                    ):
                        out = crewai_self_upgrade.run_self_upgrade(
                            db=db,
                            spec=spec,
                            actor="test",
                            run_id=f"run-active-{idx + 1}",
                            crewai_info={"importable": True},
                        )

                state = crewai_self_upgrade._read_state(out["target_id"])

        bug_lane = ((state.get("lane_states") or {}).get("bug") or {})
        self.assertEqual(bug_lane.get("status"), "active")
        self.assertEqual(bug_lane.get("zero_bug_scan_streak"), 0)
        self.assertNotIn("REPO_IMPROVEMENT_BUG_LANE_DORMANT", [event.get("event_type") for event in db.events])


if __name__ == "__main__":
    unittest.main()
