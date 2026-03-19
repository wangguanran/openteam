from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any


class YAMLError(ValueError):
    """Minimal YAML compatibility error."""


def _read_input(stream_or_text: Any) -> str:
    if hasattr(stream_or_text, "read"):
        data = stream_or_text.read()
        return data.decode("utf-8") if isinstance(data, bytes) else str(data)
    if stream_or_text is None:
        return ""
    if isinstance(stream_or_text, bytes):
        return stream_or_text.decode("utf-8")
    return str(stream_or_text)


def _strip_comment(text: str) -> str:
    in_single = False
    in_double = False
    bracket_depth = 0
    for idx, ch in enumerate(text):
        if ch == "'" and not in_double:
            in_single = not in_single
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            continue
        if in_single or in_double:
            continue
        if ch in "[{":
            bracket_depth += 1
            continue
        if ch in "]}":
            bracket_depth = max(0, bracket_depth - 1)
            continue
        if ch == "#" and bracket_depth == 0:
            return text[:idx].rstrip()
    return text.rstrip()


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _coerce_inline_collection(text: str) -> Any:
    candidate = text.strip()
    if not candidate:
        return candidate
    normalized = (
        candidate.replace(": true", ": True")
        .replace(": false", ": False")
        .replace(": null", ": None")
        .replace("[true", "[True")
        .replace("[false", "[False")
        .replace("[null", "[None")
        .replace(", true", ", True")
        .replace(", false", ", False")
        .replace(", null", ", None")
    )
    try:
        return ast.literal_eval(normalized)
    except Exception as exc:  # pragma: no cover - defensive
        raise YAMLError(f"invalid inline collection: {candidate}") from exc


def _coerce_scalar(text: str) -> Any:
    raw = _strip_comment(text).strip()
    if raw == "":
        return ""
    lowered = raw.lower()
    if lowered in {"null", "none", "~"}:
        return None
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if raw.startswith(("'", '"')) and raw.endswith(("'", '"')) and raw[0] == raw[-1]:
        try:
            return ast.literal_eval(raw)
        except Exception:
            return raw[1:-1]
    if raw.startswith("[") and raw.endswith("]"):
        return _coerce_inline_collection(raw)
    if raw.startswith("{") and raw.endswith("}"):
        return _coerce_inline_collection(raw)
    try:
        return int(raw)
    except Exception:
        pass
    try:
        if any(ch in raw for ch in (".", "e", "E")):
            return float(raw)
    except Exception:
        pass
    return raw


class _Parser:
    def __init__(self, text: str) -> None:
        self.lines = text.splitlines()
        self.idx = 0

    def parse(self) -> Any:
        self._skip_empty()
        if self.idx >= len(self.lines):
            return {}
        peek = self._peek()
        if peek is None:
            return {}
        _, line = peek
        stripped = _strip_comment(line.strip())
        if not line.lstrip().startswith("- ") and ":" not in stripped:
            self.idx += 1
            self._skip_empty()
            if self.idx < len(self.lines):
                raise YAMLError(f"unexpected content after scalar at line {self.idx + 1}")
            return _coerce_scalar(stripped)
        node = self._parse_block(indent=0)
        self._skip_empty()
        return node

    def _skip_empty(self) -> None:
        while self.idx < len(self.lines):
            stripped = self.lines[self.idx].strip()
            if not stripped or stripped.startswith("#"):
                self.idx += 1
                continue
            break

    def _peek(self) -> tuple[int, str] | None:
        self._skip_empty()
        if self.idx >= len(self.lines):
            return None
        line = self.lines[self.idx]
        return _indent_of(line), line

    def _parse_block(self, indent: int) -> Any:
        peek = self._peek()
        if peek is None:
            return {}
        current_indent, line = peek
        if current_indent < indent:
            return {}
        if line.lstrip() == "-" or line.lstrip().startswith("- "):
            return self._parse_sequence(indent)
        return self._parse_mapping(indent)

    def _parse_block_scalar(self, parent_indent: int, style: str) -> str:
        chunks: list[str] = []
        block_indent: int | None = None
        while self.idx < len(self.lines):
            line = self.lines[self.idx]
            stripped = line.strip()
            indent = _indent_of(line)
            if stripped == "":
                chunks.append("")
                self.idx += 1
                continue
            if indent <= parent_indent:
                break
            if block_indent is None:
                block_indent = indent
            if indent < block_indent:
                break
            chunks.append(line[block_indent:])
            self.idx += 1
        if style == "|":
            return "\n".join(chunks)
        folded: list[str] = []
        paragraph: list[str] = []
        for chunk in chunks:
            if chunk == "":
                if paragraph:
                    folded.append(" ".join(paragraph))
                    paragraph = []
                folded.append("")
            else:
                paragraph.append(chunk)
        if paragraph:
            folded.append(" ".join(paragraph))
        return "\n".join(folded)

    def _parse_mapping_with_seed(self, indent: int, seed: dict[str, Any]) -> dict[str, Any]:
        mapping = dict(seed)
        while True:
            peek = self._peek()
            if peek is None:
                return mapping
            current_indent, line = peek
            if current_indent < indent:
                return mapping
            if current_indent > indent:
                raise YAMLError(f"unexpected indent at line {self.idx + 1}")
            stripped = line.strip()
            if stripped.startswith("- "):
                return mapping
            self.idx += 1
            body = _strip_comment(line[current_indent:])
            if ":" not in body:
                raise YAMLError(f"expected key/value mapping at line {self.idx}")
            key, raw_value = body.split(":", 1)
            key = key.strip()
            value_text = raw_value.strip()
            if value_text in {"|", ">"}:
                mapping[key] = self._parse_block_scalar(indent, value_text)
                continue
            if value_text != "":
                mapping[key] = _coerce_scalar(value_text)
                continue
            nested = self._peek()
            if nested is None or nested[0] <= indent:
                mapping[key] = None
            else:
                mapping[key] = self._parse_block(indent + 2)

    def _parse_mapping(self, indent: int) -> dict[str, Any]:
        return self._parse_mapping_with_seed(indent, {})

    def _parse_sequence(self, indent: int) -> list[Any]:
        items: list[Any] = []
        while True:
            peek = self._peek()
            if peek is None:
                return items
            current_indent, line = peek
            if current_indent < indent:
                return items
            stripped_line = line.lstrip()
            if current_indent != indent or not (stripped_line == "-" or stripped_line.startswith("- ")):
                return items
            stripped = line.strip()[1:].lstrip()
            self.idx += 1
            if stripped == "":
                nested = self._peek()
                if nested is None or nested[0] <= indent:
                    items.append(None)
                else:
                    items.append(self._parse_block(indent + 2))
                continue
            if ":" in _strip_comment(stripped):
                key, raw_value = _strip_comment(stripped).split(":", 1)
                key = key.strip()
                value_text = raw_value.strip()
                if value_text in {"|", ">"}:
                    seed = {key: self._parse_block_scalar(indent, value_text)}
                    items.append(self._parse_mapping_with_seed(indent + 2, seed))
                    continue
                if value_text != "":
                    seed = {key: _coerce_scalar(value_text)}
                    items.append(self._parse_mapping_with_seed(indent + 2, seed))
                    continue
                nested = self._peek()
                if nested is None or nested[0] <= indent:
                    seed = {key: None}
                else:
                    seed = {key: self._parse_block(indent + 2)}
                items.append(self._parse_mapping_with_seed(indent + 2, seed))
                continue
            items.append(_coerce_scalar(stripped))


def safe_load(stream: Any) -> Any:
    text = _read_input(stream)
    return _Parser(text).parse()


def load(stream: Any, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - compatibility wrapper
    return safe_load(stream)


def _dump_scalar(value: Any, *, allow_unicode: bool) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    text = str(value)
    if text == "":
        return '""'
    if "\n" in text:
        return "|"
    plain_safe = True
    if text[0] in "-!@#&*?{|[>,%`'\" " or text[-1].isspace():
        plain_safe = False
    if any(ch in text for ch in (":", "#", "[", "]", "{", "}", ",")):
        plain_safe = False
    if text.lower() in {"null", "none", "true", "false", "yes", "no", "on", "off"}:
        plain_safe = False
    if plain_safe:
        return text
    return json.dumps(text, ensure_ascii=not allow_unicode)


def _dump_node(value: Any, *, indent: int, sort_keys: bool, allow_unicode: bool) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        items = value.items()
        if sort_keys:
            items = sorted(items, key=lambda item: str(item[0]))
        lines: list[str] = []
        for key, item in items:
            dumped_key = str(key)
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}{dumped_key}:")
                lines.extend(_dump_node(item, indent=indent + 2, sort_keys=sort_keys, allow_unicode=allow_unicode))
                continue
            scalar = _dump_scalar(item, allow_unicode=allow_unicode)
            if scalar == "|":
                lines.append(f"{prefix}{dumped_key}: |")
                for block_line in str(item).splitlines():
                    lines.append(f"{prefix}  {block_line}")
                if not str(item).splitlines():
                    lines.append(f"{prefix}  ")
                continue
            lines.append(f"{prefix}{dumped_key}: {scalar}")
        return lines
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.extend(_dump_node(item, indent=indent + 2, sort_keys=sort_keys, allow_unicode=allow_unicode))
                continue
            scalar = _dump_scalar(item, allow_unicode=allow_unicode)
            if scalar == "|":
                lines.append(f"{prefix}- |")
                for block_line in str(item).splitlines():
                    lines.append(f"{prefix}  {block_line}")
                if not str(item).splitlines():
                    lines.append(f"{prefix}  ")
                continue
            lines.append(f"{prefix}- {scalar}")
        return lines
    return [f"{prefix}{_dump_scalar(value, allow_unicode=allow_unicode)}"]


def safe_dump(data: Any, stream: Any = None, *, sort_keys: bool = True, allow_unicode: bool = False, **_: Any) -> str:
    text = "\n".join(_dump_node(data, indent=0, sort_keys=sort_keys, allow_unicode=allow_unicode)) + "\n"
    if stream is not None:
        stream.write(text)
    return text


def dump(data: Any, stream: Any = None, *args: Any, **kwargs: Any) -> str:  # pragma: no cover - compatibility wrapper
    return safe_dump(data, stream=stream, **kwargs)


__all__ = [
    "YAMLError",
    "dump",
    "load",
    "safe_dump",
    "safe_load",
]
