import os
from pathlib import Path
import sys


def data_directory():
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/delphi-code"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share").expanduser().resolve() / "delphi-code"


def model_directory():
    return data_directory() / "models/all-MiniLM-L6-v2"


def index_root():
    return Path(os.environ.get("DELPHI_CODE_INDEX_ROOT") or data_directory() / "indexes").expanduser().resolve()


def registry_path():
    return Path(os.environ.get("DELPHI_CODE_REGISTRY") or data_directory() / "repos.toml").expanduser().resolve()

