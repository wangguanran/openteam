"""team_os_logging.py -- Structured logging for team-os.

Provides a configured logger that outputs structured JSON logs.
Uses stdlib logging with a JSON formatter for zero extra dependencies.

Usage:
    from team_os_logging import get_logger
    log = get_logger(__name__)
    log.info("starting", component="control-plane", port=8787)
    log.warning("slow query", sql=sql, duration_ms=elapsed)
    log.error("operation failed", exc_info=True)
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any


class _JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Include extra fields passed via log.info("msg", extra={...})
        for key in ("component", "duration_ms", "sql", "error", "run_id",
                     "team_id", "project_id", "agent_id", "scope"):
            val = getattr(record, key, None)
            if val is not None:
                out[key] = val
        if record.exc_info and record.exc_info[1]:
            out["exception"] = str(record.exc_info[1])
        return json.dumps(out, ensure_ascii=False)


class _StructuredLogger:
    """Wrapper that allows keyword arguments in log calls."""

    def __init__(self, logger: logging.Logger):
        self._logger = logger

    def _log(self, level: int, msg: str, **kwargs: Any) -> None:
        exc_info = kwargs.pop("exc_info", False)
        self._logger.log(level, msg, exc_info=exc_info, extra=kwargs)

    def debug(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.DEBUG, msg, **kwargs)

    def info(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.INFO, msg, **kwargs)

    def warning(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.WARNING, msg, **kwargs)

    def error(self, msg: str, **kwargs: Any) -> None:
        self._log(logging.ERROR, msg, **kwargs)

    def exception(self, msg: str, **kwargs: Any) -> None:
        kwargs["exc_info"] = True
        self._log(logging.ERROR, msg, **kwargs)


_configured = False


def _configure_once() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    root = logging.getLogger("team_os")
    level_name = os.getenv("TEAMOS_LOG_LEVEL", "INFO").upper()
    root.setLevel(getattr(logging, level_name, logging.INFO))

    log_format = os.getenv("TEAMOS_LOG_FORMAT", "json").lower()
    handler = logging.StreamHandler(sys.stderr)

    if log_format == "json":
        handler.setFormatter(_JSONFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-5s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        ))

    root.addHandler(handler)
    root.propagate = False


def get_logger(name: str = "team_os") -> _StructuredLogger:
    """Get a structured logger. Name should be __name__ or a dotted path."""
    _configure_once()
    if not name.startswith("team_os"):
        name = f"team_os.{name}"
    return _StructuredLogger(logging.getLogger(name))
