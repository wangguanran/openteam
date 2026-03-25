"""Load agency-agents Markdown prompt files and map them to OpenTeam role specs."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class AgencyPrompt:
    """Parsed agency-agents prompt."""

    file_key: str
    name: str = ""
    description: str = ""
    body: str = ""
    frontmatter: dict[str, Any] = None  # type: ignore[assignment]
    source_path: str = ""

    def __post_init__(self) -> None:
        if self.frontmatter is None:
            object.__setattr__(self, "frontmatter", {})


def _agency_agents_root() -> Path:
    explicit = str(os.getenv("OPENTEAM_AGENCY_AGENTS_PATH") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    repo_root = Path(__file__).resolve().parents[5]
    return (repo_root / "vendor" / "agency-agents").resolve()


def _parse_markdown_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split YAML frontmatter and Markdown body."""
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return {}, text
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", stripped, re.S)
    if not match:
        return {}, text
    try:
        fm = yaml.safe_load(match.group(1)) or {}
    except Exception:
        fm = {}
    return (fm if isinstance(fm, dict) else {}), match.group(2)


def _file_key(path: Path, root: Path) -> str:
    """Generate a stable key from relative path (e.g. 'engineering/engineering-code-reviewer')."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = Path(path.name)
    return str(rel.with_suffix("")).replace("\\", "/")


@lru_cache(maxsize=1)
def load_all_prompts(root: Path | None = None) -> dict[str, AgencyPrompt]:
    """Load all .md prompt files from the agency-agents directory."""
    base = root or _agency_agents_root()
    prompts: dict[str, AgencyPrompt] = {}
    if not base.exists():
        return prompts
    for path in sorted(base.rglob("*.md")):
        if path.name.startswith(".") or path.name in ("README.md", "CONTRIBUTING.md", "LICENSE.md"):
            continue
        rel_parts = path.relative_to(base).parts
        if any(p.startswith(".") for p in rel_parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        fm, body = _parse_markdown_frontmatter(text)
        key = _file_key(path, base)
        prompts[key] = AgencyPrompt(
            file_key=key,
            name=str(fm.get("name") or "").strip(),
            description=str(fm.get("description") or "").strip(),
            body=body.strip(),
            frontmatter=fm,
            source_path=str(path),
        )
    return prompts


def get_prompt(file_key: str, *, root: Path | None = None) -> AgencyPrompt | None:
    """Look up a single prompt by its file key."""
    return load_all_prompts(root).get(str(file_key or "").strip())


def clear_cache() -> None:
    load_all_prompts.cache_clear()
