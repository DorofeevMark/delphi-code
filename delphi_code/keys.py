import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

from .model import Failure

SCP_STYLE_REMOTE = re.compile(r"^(?:[^@/]+@)?([^:/]+):(?!//)(.+)$")
HOSTNAME = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)*")


def normalize_remote(remote):
    remote = remote.strip()
    if "://" in remote:
        parts = urlsplit(remote)
        host, path = parts.hostname or "", parts.path
    elif match := SCP_STYLE_REMOTE.match(remote):
        host, path = match.groups()
    else:
        host, _, path = remote.partition("/")
    host, path = host.lower(), path.strip("/").removesuffix(".git").strip("/").lower()
    return f"{host}/{path}" if HOSTNAME.fullmatch(host) and path else None


def local_key(path):
    return f"local:{path}"


def project_key(path):
    if not path.is_dir():
        return local_key(path)
    repository_root, origin = git_output(path, "rev-parse", "--show-toplevel"), git_output(path, "remote", "get-url", "origin")
    is_repository_root = repository_root is not None and Path(repository_root).resolve() == path
    remote_key = normalize_remote(origin) if is_repository_root and origin else None
    return remote_key or local_key(path)


def git_output(directory, *args):
    try:
        result = subprocess.run(["git", "-C", str(directory), *args], capture_output=True, text=True, timeout=10,
                                env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def is_explicit_path(name):
    return name.startswith(("/", ".", "~"))


def names_key(name, key, project):
    wanted = name.strip("/").lower()
    key = key.lower()
    return key in {wanted, normalize_remote(name)} or key.endswith("/" + wanted) or (project is not None and project.name == name)


def match_key(name, candidates):
    if is_explicit_path(name):
        path = Path(name).expanduser().resolve()
        key = project_key(path)
        matches = {candidate for candidate, project in candidates if candidate == key or project == path}
    else:
        matches = {candidate for candidate, project in candidates if names_key(name, candidate, project)}
    if len(matches) > 1:
        raise Failure("project_ambiguous", f"Multiple projects match {name}: {', '.join(sorted(matches))}. Use a longer key or an explicit path.", 2)
    return matches.pop() if matches else None
