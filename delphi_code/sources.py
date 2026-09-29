from contextlib import AbstractContextManager, contextmanager
from functools import cached_property
import os
from pathlib import Path
import subprocess
import tempfile
from typing import NamedTuple, Protocol

from .hosts import HOSTS_BY_DOMAIN
from .keys import is_explicit_path, normalize_remote, project_key
from .model import Failure

ASKPASS_SCRIPT = """#!/bin/sh
case "$1" in
  Username*) printf '%s\\n' "$DELPHI_CODE_GIT_USERNAME" ;;
  *) printf '%s\\n' "$DELPHI_CODE_GIT_PASSWORD" ;;
esac
"""
AUTHENTICATION_FAILURES = ("Authentication failed", "could not read Username", "Invalid username or password", "403")
GIT_TIMEOUT_SECONDS = 600


class Revision(NamedTuple):
    ref: str
    commit: str


class Checkout(NamedTuple):
    directory: Path
    project: Path | None
    provenance: dict


class Source(Protocol):
    key: str

    def latest_revision(self, ref) -> Revision | None: ...

    def checkout(self, ref, revision) -> AbstractContextManager[Checkout]: ...


class LocalSource:
    def __init__(self, path):
        self.path = path

    @cached_property
    def key(self):
        return project_key(self.path)

    def latest_revision(self, ref):
        return None

    @contextmanager
    def checkout(self, ref=None, revision=None):
        if not self.path.is_dir():
            raise Failure("project_missing", f"Project directory does not exist: {self.path}", 2)
        yield Checkout(self.path, self.path, {"kind": "local", "key": self.key})


class GitRemoteSource:
    def __init__(self, host, owner, repository):
        self.host = host
        self.owner, self.repository = owner, repository
        self.key = f"{host.domain}/{owner}/{repository}"
        self.clone_url = host.clone_url(owner, repository)

    def latest_revision(self, ref):
        return self._named_revision(ref) if ref else self._default_branch_revision()

    @contextmanager
    def checkout(self, ref=None, revision=None):
        revision = revision or self.latest_revision(ref)
        with tempfile.TemporaryDirectory(prefix="delphi-code-checkout-") as temporary:
            directory = Path(temporary) / "repository"
            self._git("clone", "--quiet", "--depth", "1", "--single-branch", "--no-tags", "--branch", revision.ref, self.clone_url, str(directory))
            commit = self._git("-C", str(directory), "rev-parse", "HEAD").strip()
            yield Checkout(directory, None, {
                "kind": self.host.name.lower(), "key": self.key, "url": self.host.web_url(self.owner, self.repository),
                "ref": revision.ref, "commit": commit, "permalink": self.host.permalink_template(self.owner, self.repository, commit),
            })

    def _default_branch_revision(self):
        advertised = self._remote_refs("HEAD")
        branch = next((value.removeprefix("ref: refs/heads/") for value, name in advertised if value.startswith("ref: ") and name == "HEAD"), None)
        commit = next((value for value, name in advertised if name == "HEAD" and not value.startswith("ref: ")), None)
        if not (branch and commit):
            raise Failure("remote_empty", f"{self.key} has no default branch", 5)
        return Revision(branch, commit)

    def _named_revision(self, ref):
        commits = dict((name, value) for value, name in self._remote_refs(f"refs/heads/{ref}", f"refs/tags/{ref}"))
        commit = commits.get(f"refs/heads/{ref}") or commits.get(f"refs/tags/{ref}^{{}}") or commits.get(f"refs/tags/{ref}")
        if not commit:
            raise Failure("remote_ref_missing", f"{self.key} has no branch or tag named {ref}", 5)
        return Revision(ref, commit)

    def _remote_refs(self, *patterns):
        output = self._git("ls-remote", "--symref", self.clone_url, *patterns)
        return [line.split("\t") for line in output.splitlines()]

    def _git(self, *args):
        return run_git(args, self.key, self.host.clone_credentials(), self.host.credentials_hint)


def run_git(args, key, credentials, credentials_hint):
    environment = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    override_credential_helpers = []
    with tempfile.TemporaryDirectory(prefix="delphi-code-git-") as scratch:
        if credentials:
            askpass = Path(scratch) / "askpass"
            askpass.write_text(ASKPASS_SCRIPT)
            askpass.chmod(0o700)
            environment.update(GIT_ASKPASS=str(askpass), DELPHI_CODE_GIT_USERNAME=credentials[0], DELPHI_CODE_GIT_PASSWORD=credentials[1])
            override_credential_helpers = ["-c", "credential.helper="]
        try:
            result = subprocess.run(["git", *override_credential_helpers, *args], capture_output=True, text=True,
                                    timeout=GIT_TIMEOUT_SECONDS, env=environment)
        except FileNotFoundError as exc:
            raise Failure("git_missing", "git is required for remote repositories", 3) from exc
        except subprocess.TimeoutExpired as exc:
            raise Failure("remote_unavailable", f"git timed out after {GIT_TIMEOUT_SECONDS} seconds for {key}", 5) from exc
    if result.returncode == 0:
        return result.stdout
    last_error_line = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else f"git exited with {result.returncode}"
    if any(failure in result.stderr for failure in AUTHENTICATION_FAILURES):
        raise Failure("remote_auth_failed", f"Access to {key} was refused: {last_error_line}. To fix it, {credentials_hint}", 5)
    raise Failure("remote_unavailable", f"git failed for {key}: {last_error_line}", 5)


def source_from_registry(text):
    path = Path(text).expanduser()
    if path.is_absolute():
        return LocalSource(path.resolve())
    key = normalize_remote(text)
    host, *repository = key.split("/") if key else [None]
    if host in HOSTS_BY_DOMAIN and len(repository) == 2:
        return GitRemoteSource(HOSTS_BY_DOMAIN[host](), *repository)
    return None


def source_from_argument(text):
    path = Path(text).expanduser()
    if is_explicit_path(text) or path.is_dir():
        return LocalSource(path.resolve())
    source = source_from_registry(text)
    if source is None:
        raise Failure("usage", f"{text} is neither a project directory nor a repository such as bitbucket.org/workspace/repository or github.com/owner/repository", 2)
    return source
