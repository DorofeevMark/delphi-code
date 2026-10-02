import logging
from logging.handlers import RotatingFileHandler
import os

from .paths import log_path

PACKAGE_LOGGER = "delphi_code"
DEFAULT_LEVEL = "INFO"
THIRD_PARTY_LEVEL = logging.WARNING
MAX_BYTES_PER_FILE = 1_000_000
ROTATED_FILES_KEPT = 3
LINE_FORMAT = "%(asctime)s %(process)d %(levelname)s %(name)s: %(message)s"


def write_logs_to_file():
    level = os.environ.get("DELPHI_CODE_LOG_LEVEL", DEFAULT_LEVEL).upper()
    if level == "OFF":
        return
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            path, maxBytes=MAX_BYTES_PER_FILE, backupCount=ROTATED_FILES_KEPT, encoding="utf-8"
        )
    except OSError:
        return
    handler.setFormatter(logging.Formatter(LINE_FORMAT))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(THIRD_PARTY_LEVEL)
    logging.getLogger(PACKAGE_LOGGER).setLevel(logging.getLevelNamesMapping().get(level, logging.INFO))
