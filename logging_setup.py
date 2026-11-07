"""Structured logging: human-readable console + JSON-lines rotating file.

JSON records carry a run_id when a pipeline run is active — set via
set_log_run_id() — so log lines can be cross-referenced to the runs table.
"""
from __future__ import annotations

import contextvars
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config import PROJECT_ROOT
from utils import utcnow

_RUN_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar("run_id", default=None)


def set_log_run_id(run_id: str | None) -> None:
    _RUN_ID.set(run_id)


def get_log_run_id() -> str | None:
    return _RUN_ID.get()


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        run_id = get_log_run_id()
        if run_id:
            payload["run_id"] = run_id[:8]
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO", log_dir: Path | None = None) -> None:
    log_dir = Path(log_dir) if log_dir else PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"
    ))
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        log_dir / "engine.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(JsonFormatter())
    root.addHandler(file_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)