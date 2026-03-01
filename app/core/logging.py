import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


DEFAULT_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def _parse_level() -> int:
    level_name = os.getenv("LOG_LEVEL", "INFO").upper().strip()
    return getattr(logging, level_name, logging.INFO)


def configure_logging() -> None:
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return

    level = _parse_level()
    formatter = logging.Formatter(DEFAULT_LOG_FORMAT, datefmt=DEFAULT_DATE_FORMAT)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    handlers: list[logging.Handler] = [stream_handler]

    log_file = os.getenv("LOG_FILE", "")
    if log_file:
        log_path = Path(log_file).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    logging.basicConfig(level=level, handlers=handlers)

    # Reduce very noisy libraries by default.
    logging.getLogger("aiogram").setLevel(max(level, logging.INFO))
    logging.getLogger("telethon").setLevel(max(level, logging.INFO))
    logging.getLogger("sqlalchemy.engine").setLevel(max(level, logging.WARNING))
