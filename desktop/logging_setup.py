"""Logging and crash handling for NeonVeil.

The desktop writes a rotating log file so that unexpected errors can be
diagnosed after the fact, and installs an exception hook that records
uncaught exceptions instead of letting the session die silently.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable

LOGGER_NAME = "neonveil"
LOG_FILE_NAME = "neonveil.log"
DEFAULT_LOG_DIR = Path.home() / ".local" / "share" / "NeonVeil" / "logs"

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def log_file_path(log_dir: Path | str | None = None) -> Path:
    """Return the full path of the NeonVeil log file."""
    return Path(log_dir).expanduser() if log_dir else DEFAULT_LOG_DIR


def setup_logging(
    level: int = logging.INFO,
    log_dir: Path | str | None = None,
    *,
    force: bool = False,
) -> logging.Logger:
    """Configure and return the NeonVeil logger.

    A stream handler is always installed. A rotating file handler is added on
    a best-effort basis; if the log directory is not writable the desktop still
    runs and simply logs to stderr.
    """
    logger = logging.getLogger(LOGGER_NAME)
    if getattr(logger, "_neonveil_configured", False) and not force:
        return logger
    logger.setLevel(level)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # pragma: no cover - defensive
            pass

    formatter = logging.Formatter(_FORMAT)
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)

    try:
        directory = log_file_path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            directory / LOG_FILE_NAME,
            maxBytes=512 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError as error:
        logger.warning("Protokolldatei kann nicht angelegt werden: %s", error)

    logger._neonveil_configured = True  # type: ignore[attr-defined]
    return logger


def install_qt_message_handler(logger: logging.Logger | None = None) -> object | None:
    """Route Qt log messages into the NeonVeil log. Best-effort."""
    log = logger or logging.getLogger(LOGGER_NAME)
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    except ImportError:  # pragma: no cover - Qt always present on the desktop
        return None
    levels = {
        QtMsgType.QtDebugMsg: logging.DEBUG,
        QtMsgType.QtInfoMsg: logging.INFO,
        QtMsgType.QtWarningMsg: logging.WARNING,
        QtMsgType.QtCriticalMsg: logging.ERROR,
        QtMsgType.QtFatalMsg: logging.CRITICAL,
    }

    def handler(mode, context, message) -> None:
        log.log(levels.get(mode, logging.INFO), "Qt: %s", message)

    qInstallMessageHandler(handler)
    return handler


def install_exception_hook(
    on_error: Callable[[type[BaseException], BaseException, object], None] | None = None,
    logger: logging.Logger | None = None,
) -> Callable:
    """Install a ``sys.excepthook`` that logs uncaught exceptions.

    ``on_error`` is called after logging so the caller can surface a dialog.
    """
    log = logger or logging.getLogger(LOGGER_NAME)

    def hook(exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        log.critical("Unbehandelte Ausnahme", exc_info=(exc_type, exc_value, exc_tb))
        if on_error is not None:
            try:
                on_error(exc_type, exc_value, exc_tb)
            except Exception:  # pragma: no cover - never mask the original error
                log.exception("Fehler im Ausnahme-Handler")

    sys.excepthook = hook
    return hook
