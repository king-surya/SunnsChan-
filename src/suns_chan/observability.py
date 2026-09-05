"""Structured logging + minimal tracing. Stdlib only."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from pathlib import Path
import sys
from typing import Any


_CONFIGURED = False


def setup_logging(level: str = "INFO", log_path: str | None = None) -> logging.Logger:
    """Idempotent logging setup. Console + optional file. Returns root suns_chan logger."""
    global _CONFIGURED
    logger = logging.getLogger("suns_chan")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    if _CONFIGURED:
        return logger
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    logger.propagate = False
    _CONFIGURED = True
    return logger


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(f"suns_chan.{name}")


def log_event(logger: logging.Logger, event: str, **fields: Any) -> None:
    logger.info("%s %s", event, json.dumps(fields, sort_keys=True, default=str))


@dataclass
class TraceStep:
    name: str
    detail: str = ""
    children: list["TraceStep"] = field(default_factory=list)

    def add(self, name: str, detail: str = "") -> "TraceStep":
        child = TraceStep(name, detail)
        self.children.append(child)
        return child

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "detail": self.detail,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class TaskTrace:
    """Task -> Plan -> Step -> Tool -> Result -> Observation -> Decision."""

    task: str
    root: TraceStep = field(default_factory=lambda: TraceStep("task"))

    def step(self, name: str, detail: str = "") -> TraceStep:
        return self.root.add(name, detail)


def health_check() -> dict:
    return {"status": "ok", "service": "suns-chan"}
