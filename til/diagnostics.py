import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


_LOGGER = None


def _log_path():
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or "."
    path = Path(root) / "TIL" / "til.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def logger():
    global _LOGGER
    if _LOGGER is None:
        log = logging.getLogger("TIL")
        log.setLevel(logging.INFO)
        log.propagate = False
        try:
            handler = RotatingFileHandler(_log_path(), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(message)s"))
            log.addHandler(handler)
        except Exception:
            log.addHandler(logging.NullHandler())
        _LOGGER = log
    return _LOGGER


def info(message):
    logger().info(message)


def warning(message):
    logger().warning(message)


def exception(context, error=None):
    # Do not persist exception text: it may contain selected text or provider details.
    logger().error("%s [%s]", context, type(error).__name__ if error else "Exception", exc_info=error is not None)
