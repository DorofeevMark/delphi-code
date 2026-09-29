import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

from .errors import ExitCode, Failure
from .model import incidental
from .paths import index_root


def verify_assets(root):
    manifest = json.loads(Path(__file__).with_name("model_provenance.json").read_text())
    for name, expected in manifest["sha256"].items():
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise Failure("model_invalid", f"Missing or linked model asset: {path}", ExitCode.RUNTIME_ASSETS)
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise Failure("model_invalid", f"Model asset checksum mismatch: {path}", ExitCode.RUNTIME_ASSETS)
    allowed = set(manifest["sha256"]) | {"provenance.json"}
    for path in root.rglob("*"):
        if not incidental(path.relative_to(root)) and path.is_file() and path.relative_to(root).as_posix() not in allowed:
            raise Failure("model_invalid", f"Unexpected model asset: {path}", ExitCode.RUNTIME_ASSETS)
    return manifest


def download(destination):
    offline_flags = {"HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"}
    env = {name: value for name, value in os.environ.items() if name not in offline_flags}
    result = subprocess.run(
        [sys.executable, str(Path(__file__).with_name("download.py")), str(destination)],
        env=env, stdout=sys.stderr, stderr=sys.stderr,
    )
    if result.returncode:
        raise Failure("setup_download_failed", "Model download failed; check connectivity and rerun delphi-code setup, or use delphi-code setup --from /path/to/model", ExitCode.RUNTIME_ASSETS)


def diagnose(model):
    from .cli import execute

    root = index_root()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=root):
        pass
    with tempfile.TemporaryDirectory(prefix=".setup-probe-", dir=root.parent) as scratch:
        from .indexing import check_storage
        import asyncio

        asyncio.run(check_storage(Path(scratch)))
    return execute(argparse.Namespace(command="doctor", project=str(Path.cwd()), model=str(model)))


def provision(args):
    destination = Path(args.model).expanduser().resolve()
    source = Path(args.source).expanduser().resolve() if args.source else None
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (destination.parent / f".{destination.name}.setup.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Failure("setup_busy", "Another setup is running for this model; retry when it finishes", ExitCode.OPERATION) from exc
        if destination.exists():
            try:
                verify_assets(destination)
            except Failure as exc:
                raise Failure(exc.code, f"{exc}. Existing assets were preserved. Move {destination} aside, then rerun delphi-code setup --model {shlex.quote(str(destination))}", ExitCode.RUNTIME_ASSETS) from exc
            diagnostics = diagnose(destination)
            reused = True
        else:
            with tempfile.TemporaryDirectory(prefix=f".{destination.name}-", dir=destination.parent) as temporary:
                staged = Path(temporary) / "model"
                if source:
                    verify_assets(source)
                    shutil.copytree(source, staged, ignore=shutil.ignore_patterns(".*"))
                else:
                    staged.mkdir()
                    print("Downloading the pinned MiniLM model…", file=sys.stderr)
                    download(staged)
                manifest = verify_assets(staged)
                (staged / "provenance.json").write_text(json.dumps(manifest, indent=2) + "\n")
                diagnostics = diagnose(staged)
                staged.rename(destination)
            diagnostics["model"] = str(destination)
            reused = False
    return {"model": str(destination), "index_root": str(index_root()), "reused": reused,
            "diagnostics": diagnostics}
