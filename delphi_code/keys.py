"""Project keys: the stable identity that decides which index a project uses."""
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

from .model import Failure

SCP_REMOTE = re.compile(r"^(?:[^@/]+@)?([^:/]+):(?!//)(.+)$")
HOST = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)*")


def canonical(remote):
    """Normalize a Git remote to host/owner/repo so SSH and HTTPS clones share one key."""
    remote = remote.strip()
    if "://" in remote:
        parts = urlsplit(remote)
        host, path = parts.hostname or "", parts.path
    elif match := SCP_REMOTE.match(remote):
        host, path = match.groups()
    else:
        host, _, path = remote.partition("/")
    host, path = host.lower(), path.strip("/").removesuffix(".git").strip("/").lower()
    return f"{host}/{path}" if HOST.fullmatch(host) and path else None


def local_key(project):
    return f"local:{project}"


def project_key(project):
    """A Git repository root is keyed by its origin remote; anything else by its resolved path."""
    def git(*args):
        result = subprocess.run(["git", "-C", str(project), *args], capture_output=True, text=True, timeout=10,
                                env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
        return result.stdout.strip() if result.returncode == 0 else None

    try:
        top, remote = git("rev-parse", "--show-toplevel"), git("remote", "get-url", "origin")
    except (OSError, subprocess.TimeoutExpired):
        return local_key(project)
    if top and remote and Path(top).resolve() == project:
        return canonical(remote) or local_key(project)
    return local_key(project)


def key_for(path):
    return project_key(path) if path.is_dir() else local_key(path)


def explicit_path(raw):
    return raw.startswith(("/", ".", "~"))


def select(raw, candidates):
    """Picks the one key among (key, project path) candidates that raw names.

    An explicit path matches its own key or the project recorded at that path; anything else
    matches a whole key, a trailing part of one (acme/api, api), or a project folder name.
    """
    if explicit_path(raw):
        path = Path(raw).expanduser().resolve()
        key = key_for(path)
        hits = {candidate for candidate, project in candidates if candidate == key or project == path}
    else:
        wanted, remote = raw.strip("/").lower(), canonical(raw)
        hits = {candidate for candidate, project in candidates
                if candidate.lower() in {wanted, remote} or candidate.lower().endswith("/" + wanted)
                or (project is not None and project.name == raw)}
    if len(hits) > 1:
        raise Failure("project_ambiguous", f"Multiple projects match {raw}: {', '.join(sorted(hits))}. Use a longer key or an explicit path.", 2)
    return hits.pop() if hits else None
