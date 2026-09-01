"""Shared logging configuration for command-line entry points."""

from __future__ import annotations

import logging
import os
import sys


DEFAULT_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def configure_logging(level: str | None = None) -> None:
    """Configure standard logging without exposing application secrets."""
    configured_level = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    numeric_level = getattr(logging, configured_level, None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Unsupported log level: {configured_level}")

    logging.basicConfig(
        level=numeric_level,
        format=DEFAULT_LOG_FORMAT,
        stream=sys.stdout,
        force=True,
    )

