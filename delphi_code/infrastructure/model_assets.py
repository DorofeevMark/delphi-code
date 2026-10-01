import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from ..domain.errors import ExitCode, Failure
from .model import is_incidental_asset

PROVENANCE_FILE = Path(__file__).with_name("model_provenance.json")
DOWNLOADER = Path(__file__).with_name("model_download.py")
ONLINE_ONLY_FLAGS = {"HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"}


def verify_assets(root: Path) -> dict:
    provenance = json.loads(PROVENANCE_FILE.read_text())
    for name, expected in provenance["sha256"].items():
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise Failure("model_invalid", f"Missing or linked model asset: {path}", ExitCode.RUNTIME_ASSETS)
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise Failure("model_invalid", f"Model asset checksum mismatch: {path}", ExitCode.RUNTIME_ASSETS)
    allowed = set(provenance["sha256"]) | {"provenance.json"}
    for path in root.rglob("*"):
        if (
            not is_incidental_asset(path.relative_to(root))
            and path.is_file()
            and path.relative_to(root).as_posix() not in allowed
        ):
            raise Failure("model_invalid", f"Unexpected model asset: {path}", ExitCode.RUNTIME_ASSETS)
    return provenance


def download(destination: Path):
    env = {name: value for name, value in os.environ.items() if name not in ONLINE_ONLY_FLAGS}
    result = subprocess.run(
        [sys.executable, str(DOWNLOADER), str(destination)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode:
        last_error_line = (result.stderr or "").strip().splitlines()[-1:] or [f"exited with {result.returncode}"]
        raise Failure(
            "setup_download_failed",
            f"Model download failed ({last_error_line[0]}); check connectivity and rerun delphi-code setup, or use delphi-code setup --from /path/to/model",
            ExitCode.RUNTIME_ASSETS,
        )
