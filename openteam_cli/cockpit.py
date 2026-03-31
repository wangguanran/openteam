from __future__ import annotations

import argparse
import threading
import urllib.request
from typing import Any

from ._shared import _base_url
from .cockpit_commands import execute_input, load_request
from .cockpit_state import build_snapshot, route_input
from .http import _iter_sse_events
from .team import _format_team_watch_event

try:
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal
    from textual.widgets import Footer, Header, Input, Static
except ModuleNotFoundError:  # pragma: no cover - depends on local optional install
    App = None  # type: ignore[assignment]
    ComposeResult = Any
    Horizontal = None
    Footer = None
    Header = None
    Input = None
    Static = None


if App is not None:

    def _render_left(snapshot: Any) -> str:
        lines = ["agents"]
        for item in snapshot.left:
            lines.append(f"- {item.agent_id or item.role or 'agent'} [{item.status or 'idle'}] {item.model or '-'}")
        return "\n".join(lines)


    def _render_center(snapshot: Any) -> str:
        lines = ["panel"]
        for item in snapshot.center[-12:]:
            head = " | ".join(x for x in (item.actor, item.role, item.stage, item.category) if x)
            lines.append(f"[{head or 'message'}] {item.text}")
        return "\n".join(lines)


    def _render_right(snapshot: Any) -> str:
        right = snapshot.right
        lines = [
            "status",
            f"request={right.request_id or '-'}",
            f"stage={right.stage or '-'}",
            f"needs_you={str(bool(right.needs_you)).lower()}",
            f"blocked={str(bool(right.blocked)).lower()}",
            f"review_gate={right.review_gate or '-'}",
            f"ci={right.ci or '-'}",
            f"pr={right.pr or '-'}",
        ]
        if right.workstreams:
            lines.append("workstreams=")
            for name, state in right.workstreams.items():
                lines.append(f"- {name}: {state}")
        return "\n".join(lines)

    class DeliveryCockpitApp(App[None]):
        BINDINGS = [("q", "quit", "Quit")]

        def __init__(self, *, project: str = "", team: str = "delivery-studio", request_id: str = "", base_url: str = "") -> None:
            super().__init__()
            self.project = str(project or "")
            self.team = str(team or "delivery-studio")
            self.request_id = str(request_id or "")
            self.base_url = str(base_url or "")
            self.run_id = ""
            self._watch_thread: threading.Thread | None = None
            self._snapshot = build_snapshot(
                request={
                    "request_id": self.request_id,
                    "stage": "",
                    "needs_you": False,
                    "blocked": False,
                    "review_gate": "",
                    "ci": "",
                    "pr": "",
                    "workstreams": {},
                },
                agents=[],
                messages=[],
            )

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Horizontal():
                yield Static(f"agents\nteam={self.team}", id="left-pane")
                yield Static("panel", id="center-pane")
                yield Static(f"status\nproject={self.project or '-'}", id="right-pane")
            yield Input(placeholder="Talk to panel, use @agent, or /propose /approve /review /watch", id="command-line")
            yield Footer()

        def on_mount(self) -> None:
            self._append_message(
                {
                    "actor": "system",
                    "role": "Cockpit",
                    "model": "",
                    "stage": "",
                    "category": "Action",
                    "text": f"team={self.team} project={self.project or '-'} request={self.request_id or '-'}",
                }
            )
            if self.request_id:
                try:
                    request = load_request(base_url=self.base_url, team_id=self.team, request_id=self.request_id)
                    self._set_request(request)
                except Exception as exc:
                    self._append_message(
                        {
                            "actor": "system",
                            "role": "Cockpit",
                            "model": "",
                            "stage": "",
                            "category": "Alert",
                            "text": f"load request failed: {exc}",
                        }
                    )
            self._refresh_panes()

        def on_input_submitted(self, event: Input.Submitted) -> None:
            raw = str(event.value or "").strip()
            event.input.value = ""
            if not raw:
                return
            try:
                routed = route_input(raw)
                out = execute_input(
                    base_url=self.base_url,
                    team_id=self.team,
                    project_id=self.project,
                    request_id=self.request_id,
                    explicit_run_id=self.run_id,
                    routed=routed,
                )
                self._handle_command_output(out)
            except Exception as exc:
                self._append_message(
                    {
                        "actor": "system",
                        "role": "Cockpit",
                        "model": "",
                        "stage": "",
                        "category": "Alert",
                        "text": str(exc),
                    }
                )
                self._refresh_panes()

        def _handle_command_output(self, out: dict[str, Any]) -> None:
            kind = str(out.get("kind") or "")
            if kind == "request":
                request = dict(out.get("request") or {})
                self._set_request(request)
                msg = dict(out.get("message") or {})
                if msg:
                    self._append_message(
                        {
                            "actor": "system",
                            "role": "Cockpit",
                            "model": "",
                            "stage": str(request.get("stage") or ""),
                            "category": str(msg.get("category") or "Action"),
                            "text": str(msg.get("text") or ""),
                        }
                    )
                self._refresh_panes()
                return
            if kind == "message":
                message = dict(out.get("message") or {})
                if str(message.get("target_agent") or "").strip():
                    self._ensure_agent(str(message.get("target_agent") or ""), status="focused")
                self._append_message(message)
                self._refresh_panes()
                return
            if kind == "watch":
                run_id = str(out.get("run_id") or "").strip()
                timeout_sec = int(out.get("timeout_sec") or 30)
                self.run_id = run_id
                self._append_message(
                    {
                        "actor": "system",
                        "role": "Cockpit",
                        "model": "",
                        "stage": "",
                        "category": "Action",
                        "text": f"watching run {run_id} for {timeout_sec}s",
                    }
                )
                self._refresh_panes()
                self._start_watch(run_id=run_id, timeout_sec=timeout_sec)
                return
            self._refresh_panes()

        def _start_watch(self, *, run_id: str, timeout_sec: int) -> None:
            if self._watch_thread is not None and self._watch_thread.is_alive():
                return

            def _worker() -> None:
                url = self.base_url.rstrip("/") + f"/v1/runs/{run_id}/stream"
                req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
                with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                    for item in _iter_sse_events(resp):
                        event_name = str(item.get("event") or "")
                        data = item.get("data") if isinstance(item.get("data"), dict) else {}
                        self.call_from_thread(self._apply_watch_event, event_name, data)

            self._watch_thread = threading.Thread(target=_worker, name="cockpit-watch", daemon=True)
            self._watch_thread.start()

        def _apply_watch_event(self, event_name: str, data: dict[str, Any]) -> None:
            if event_name == "agent":
                role_id = str(data.get("role_id") or "agent")
                state = str(data.get("state") or "running")
                self._ensure_agent(role_id, status=state)
                self._append_message(
                    {
                        "actor": role_id,
                        "role": role_id,
                        "model": "",
                        "stage": "",
                        "category": "Action",
                        "text": f"state={state} task={data.get('task_id','')} action={data.get('current_action','')}",
                    }
                )
            elif event_name == "runtime_event":
                self._append_message(
                    {
                        "actor": "runtime",
                        "role": "Runtime",
                        "model": "",
                        "stage": "",
                        "category": "Action",
                        "text": _format_team_watch_event(data),
                    }
                )
            elif event_name == "run":
                run = data.get("run") if isinstance(data.get("run"), dict) else {}
                self.run_id = str(run.get("run_id") or self.run_id)
                self._append_message(
                    {
                        "actor": "run",
                        "role": "Run",
                        "model": "",
                        "stage": "",
                        "category": "Action",
                        "text": f"run_id={run.get('run_id','')} state={run.get('state','')}",
                    }
                )
            elif event_name == "end":
                self._append_message(
                    {
                        "actor": "run",
                        "role": "Run",
                        "model": "",
                        "stage": "",
                        "category": "Action",
                        "text": f"watch ended state={data.get('state') or (data.get('run') or {}).get('state') or 'DONE'}",
                    }
                )
            self._refresh_panes()

        def _set_request(self, request: dict[str, Any]) -> None:
            self.request_id = str(request.get("request_id") or self.request_id)
            self._snapshot = build_snapshot(
                request=request,
                agents=[item.__dict__ for item in self._snapshot.left],
                messages=[item.__dict__ for item in self._snapshot.center],
            )

        def _ensure_agent(self, agent_id: str, *, status: str) -> None:
            agents = [item.__dict__ for item in self._snapshot.left]
            for item in agents:
                if str(item.get("agent_id") or "") == agent_id:
                    item["status"] = status
                    self._snapshot = build_snapshot(
                        request=self._snapshot.right.__dict__ | {"workstreams": dict(self._snapshot.right.workstreams)},
                        agents=agents,
                        messages=[item.__dict__ for item in self._snapshot.center],
                    )
                    return
            agents.append({"agent_id": agent_id, "role": agent_id, "model": "", "status": status})
            self._snapshot = build_snapshot(
                request=self._snapshot.right.__dict__ | {"workstreams": dict(self._snapshot.right.workstreams)},
                agents=agents,
                messages=[item.__dict__ for item in self._snapshot.center],
            )

        def _append_message(self, message: dict[str, Any]) -> None:
            messages = [item.__dict__ for item in self._snapshot.center]
            messages.append(
                {
                    "actor": str(message.get("actor") or ""),
                    "role": str(message.get("role") or ""),
                    "model": str(message.get("model") or ""),
                    "stage": str(message.get("stage") or ""),
                    "category": str(message.get("category") or ""),
                    "text": str(message.get("text") or ""),
                }
            )
            self._snapshot = build_snapshot(
                request=self._snapshot.right.__dict__ | {"workstreams": dict(self._snapshot.right.workstreams)},
                agents=[item.__dict__ for item in self._snapshot.left],
                messages=messages,
            )

        def _refresh_panes(self) -> None:
            self.query_one("#left-pane", Static).update(_render_left(self._snapshot))
            self.query_one("#center-pane", Static).update(_render_center(self._snapshot))
            self.query_one("#right-pane", Static).update(_render_right(self._snapshot))

else:

    class DeliveryCockpitApp:
        def __init__(self, *, project: str = "", team: str = "delivery-studio", request_id: str = "", base_url: str = "") -> None:
            self.project = str(project or "")
            self.team = str(team or "delivery-studio")
            self.request_id = str(request_id or "")
            self.base_url = str(base_url or "")

        def run(self) -> None:
            raise RuntimeError("textual is required to run `openteam cockpit`")


def cmd_cockpit(args: argparse.Namespace) -> None:
    project = str(getattr(args, "project", "") or "")
    team = str(getattr(args, "team", "delivery-studio") or "delivery-studio")
    request_id = str(getattr(args, "request_id", "") or "")
    base, _prof = _base_url(args)
    DeliveryCockpitApp(project=project, team=team, request_id=request_id, base_url=base).run()
