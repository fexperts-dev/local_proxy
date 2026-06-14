"""Logging configuration for local_proxy."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

log = logging.getLogger("local_proxy")


def setup_logging(
    level: str,
    log_file: str | None,
    *,
    to_console: bool = True,
    to_file: bool = True,
    extra_handlers: list[logging.Handler] | None = None,
    enabled_filter: logging.Filter | None = None,
) -> None:
    """Configure root logging (console, file, optional extra handlers)."""
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())

    handlers: list[logging.Handler] = []
    if to_console:
        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        handlers.append(stream)
    if to_file and log_file:
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        handlers.append(file_handler)
    if extra_handlers:
        for handler in extra_handlers:
            handler.setFormatter(fmt)
            handlers.append(handler)

    for handler in handlers:
        if enabled_filter is not None:
            handler.addFilter(enabled_filter)
        root.addHandler(handler)

    if to_file and log_file:
        log.info("Logging to %s", log_file)
