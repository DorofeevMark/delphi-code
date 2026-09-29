import json
import os
from pathlib import Path
import subprocess
import sys
from typing import NamedTuple

from .model import Failure

CATALOG_TIMEOUT_SECONDS = 300


class Workspace(NamedTuple):
    slug: str
    name: str


class Repository(NamedTuple):
    workspace: str
    slug: str
    description: str
    private: bool

    @property
    def key(self):
        return f"bitbucket.org/{self.workspace}/{self.slug}".lower()


def _api_credentials():
    username = os.environ.get("BITBUCKET_EMAIL") or os.environ.get("BITBUCKET_USERNAME")
    password = os.environ.get("BITBUCKET_APP_PASSWORD")
    if username and password:
        return username, password
    stored = _git_stored_credentials("bitbucket.org")
    if stored:
        return stored
    raise Failure("remote_auth_missing", "Listing Bitbucket repositories needs credentials: set BITBUCKET_USERNAME and BITBUCKET_APP_PASSWORD, or store git credentials for bitbucket.org", 3)


def _git_stored_credentials(host):
    environment = {name: value for name, value in os.environ.items() if name not in {"GIT_ASKPASS", "SSH_ASKPASS"}}
    environment["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(["git", "credential", "fill"], input=f"protocol=https\nhost={host}\n\n",
                                capture_output=True, text=True, timeout=30, env=environment)
    except (OSError, subprocess.TimeoutExpired):
        return None
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if result.returncode or not fields.get("username") or not fields.get("password"):
        return None
    return fields["username"], fields["password"]


class BitbucketCatalog:
    def __init__(self, credentials=None):
        self._credentials = credentials or _api_credentials()

    def workspaces(self):
        return [Workspace(item["slug"], item["name"]) for item in self._request("workspaces")]

    def repositories(self, workspace):
        return [Repository(workspace, item["slug"], item["description"], item["private"]) for item in self._request("repositories", workspace)]

    def _request(self, *arguments):
        username, password = self._credentials
        environment = dict(os.environ, DELPHI_CODE_BITBUCKET_API_USERNAME=username, DELPHI_CODE_BITBUCKET_API_PASSWORD=password)
        script = Path(__file__).with_name("bitbucket_api.py")
        try:
            result = subprocess.run([sys.executable, str(script), *arguments], capture_output=True, text=True,
                                    timeout=CATALOG_TIMEOUT_SECONDS, env=environment)
        except subprocess.TimeoutExpired as exc:
            raise Failure("remote_unavailable", f"Bitbucket did not answer within {CATALOG_TIMEOUT_SECONDS} seconds", 5) from exc
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            payload = {"error": {"status": None, "message": result.stderr.strip() or "Bitbucket API request failed"}}
        if result.returncode == 0:
            return payload
        error = payload.get("error", {})
        if error.get("status") in (401, 403):
            raise Failure("remote_auth_failed", f"Bitbucket refused the credentials: {error['message']}. With an API token, set BITBUCKET_EMAIL to your Atlassian account email", 5)
        raise Failure("remote_unavailable", f"Bitbucket API request failed: {error.get('message')}", 5)
