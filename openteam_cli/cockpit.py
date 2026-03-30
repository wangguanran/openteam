from __future__ import annotations

import argparse
from typing import Any

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

    class DeliveryCockpitApp(App[None]):
        BINDINGS = [("q", "quit", "Quit")]

        def __init__(self, *, project: str = "", team: str = "delivery-studio") -> None:
            super().__init__()
            self.project = str(project or "")
            self.team = str(team or "delivery-studio")

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Horizontal():
                yield Static(f"agents\nteam={self.team}", id="left-pane")
                yield Static("panel", id="center-pane")
                yield Static(f"status\nproject={self.project or '-'}", id="right-pane")
            yield Input(placeholder="Talk to panel, use @agent, or /approve /review /watch", id="command-line")
            yield Footer()

else:

    class DeliveryCockpitApp:
        def __init__(self, *, project: str = "", team: str = "delivery-studio") -> None:
            self.project = str(project or "")
            self.team = str(team or "delivery-studio")

        def run(self) -> None:
            raise RuntimeError("textual is required to run `openteam cockpit`")


def cmd_cockpit(args: argparse.Namespace) -> None:
    project = str(getattr(args, "project", "") or "")
    team = str(getattr(args, "team", "delivery-studio") or "delivery-studio")
    DeliveryCockpitApp(project=project, team=team).run()
