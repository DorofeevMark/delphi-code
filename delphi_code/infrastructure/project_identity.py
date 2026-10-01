import os
from pathlib import Path
import subprocess

from ..domain.keys import local_key, normalize_remote


def project_key(path):
    if not path.is_dir():
        return local_key(path)
    repository_root, origin = (
        git_output(path, "rev-parse", "--show-toplevel"),
        git_output(path, "remote", "get-url", "origin"),
    )
    is_repository_root = repository_root is not None and Path(repository_root).resolve() == path
    remote_key = normalize_remote(origin) if is_repository_root and origin else None
    return remote_key or local_key(path)


def git_output(directory, *args):
    try:
        result = subprocess.run(
            ["git", "-C", str(directory), *args],
            capture_output=True,
            text=True,
            timeout=10,
            env=dict(os.environ, GIT_TERMINAL_PROMPT="0"),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None
