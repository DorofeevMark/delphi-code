from collections.abc import Callable
from pathlib import Path
import re
from urllib.parse import urlsplit

from .errors import ExitCode, Failure

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


def is_explicit_path(name):
    return name.startswith(("/", ".", "~"))


def names_key(name, key, project):
    wanted = name.strip("/").lower()
    key = key.lower()
    return (
        key in {wanted, normalize_remote(name)}
        or key.endswith("/" + wanted)
        or (project is not None and project.name == name)
    )


def match_key(name, candidates, project_key_of_path: Callable[[Path], str]):
    if is_explicit_path(name):
        path = Path(name).expanduser().resolve()
        key = project_key_of_path(path)
        matches = {candidate for candidate, project in candidates if candidate == key or project == path}
    else:
        matches = {candidate for candidate, project in candidates if names_key(name, candidate, project)}
    if len(matches) > 1:
        raise Failure(
            "project_ambiguous",
            f"Multiple projects match {name}: {', '.join(sorted(matches))}. Use a longer key or an explicit path.",
            ExitCode.USAGE,
        )
    return matches.pop() if matches else None
