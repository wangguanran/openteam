#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from typing import Any, Optional


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _utc_today() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).date().isoformat()


def _md(title: str, body: str) -> str:
    return f"# {title}\n\n{body.strip()}\n"


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Generate a weekly evolution report from Team OS metrics/logs")
    ap.add_argument("--out", default=str(_repo_root() / ".team-os" / "ledger" / "team_workflow" / f"weekly_{_utc_today()}.md"))
    ap.add_argument("--tasks-dir", default=str(_repo_root() / ".team-os" / "logs" / "tasks"))
    args = ap.parse_args(argv)

    from .collect_from_logs import collect  # type: ignore
    from .analyze_evolution import analyze  # type: ignore

    summary = collect(Path(args.tasks_dir), strict=False)
    analysis = analyze(summary, tasks_dir=Path(args.tasks_dir))

    findings = analysis.get("findings") or []
    lines: list[str] = []
    lines.append(f"- 日期(UTC)：{_utc_today()}")
    lines.append(f"- metrics_files：{summary.get('metrics_files')} total_events：{summary.get('total_events')}")
    lines.append("\n## Findings\n")
    if not findings:
        lines.append("- (none)")
    else:
        for f in findings[:20]:
            lines.append(f"- {f.get('kind')}: {f.get('title')}")
    lines.append("\n## Raw (truncated)\n")
    lines.append("```json")
    lines.append(json.dumps({"summary": summary, "analysis": analysis}, ensure_ascii=False, indent=2)[:6000])
    lines.append("```\n")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_md("Team OS Weekly Evolution Report", "\n".join(lines)), encoding="utf-8")
    print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
