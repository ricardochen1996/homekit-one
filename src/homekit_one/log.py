"""Logging setup with an in-memory ring buffer for the web UI."""

from __future__ import annotations

import logging
import os
from collections import deque
from dataclasses import dataclass
from datetime import datetime


@dataclass
class LogRecord:
    time: str
    level: str
    logger: str
    message: str


class RingBufferHandler(logging.Handler):
    def __init__(self, capacity: int = 500) -> None:
        super().__init__()
        self.records: deque[LogRecord] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            message = record.getMessage()
        self.records.append(
            LogRecord(
                time=datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
                level=record.levelname,
                logger=record.name,
                message=message,
            )
        )


ring_buffer = RingBufferHandler()


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        stream = logging.StreamHandler()
        stream.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )
        root.addHandler(stream)
    ring_buffer.setFormatter(logging.Formatter("%(message)s"))
    if ring_buffer not in root.handlers:
        root.addHandler(ring_buffer)
    # These libraries are chatty at INFO.
    if os.environ.get("HOMEKIT_ONE_DEBUG_PYHAP"):
        logging.getLogger("pyhap").setLevel(logging.DEBUG)
    logging.getLogger("zeroconf").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
