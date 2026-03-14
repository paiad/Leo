import os
import sys
from pathlib import Path

from loguru import logger as _logger

from app.config import PROJECT_ROOT


_print_level = "INFO"


def define_log_level(print_level="INFO", logfile_level="DEBUG", name: str = None):
    """Adjust the log level to above level"""
    global _print_level
    _print_level = print_level

    base_dir = Path(os.getenv("LOG_DIR", str(PROJECT_ROOT / "logs" / "app"))).expanduser()
    base_dir.mkdir(parents=True, exist_ok=True)
    log_filename = f"{name}.log" if name else os.getenv("LOG_FILE_NAME", "app.log")
    log_file = base_dir / log_filename
    rotation = os.getenv("LOG_ROTATION", "20 MB")
    retention = os.getenv("LOG_RETENTION", "14 days")
    compression = os.getenv("LOG_COMPRESSION", "zip")

    _logger.remove()
    _logger.add(sys.stderr, level=print_level)
    _logger.add(
        log_file,
        level=logfile_level,
        rotation=rotation,
        retention=retention,
        compression=compression,
        enqueue=True,
        backtrace=True,
        diagnose=False,
    )
    return _logger


logger = define_log_level()


if __name__ == "__main__":
    logger.info("Starting application")
    logger.debug("Debug message")
    logger.warning("Warning message")
    logger.error("Error message")
    logger.critical("Critical message")

    try:
        raise ValueError("Test error")
    except Exception as e:
        logger.exception(f"An error occurred: {e}")
