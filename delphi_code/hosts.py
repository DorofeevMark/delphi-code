import base64
from functools import cached_property
import json
import os
import re
import ssl
import subprocess
import sys
from typing import Any, NamedTuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

if __package__:
    from .errors import ExitCode, Failure

PAGE_LENGTH = 100
REQUEST_TIMEOUT_SECONDS = 60
LISTING_TIMEOUT_SECONDS = 300


class Owner(NamedTuple):
    slug: str
    name: str


class Repository(NamedTuple):
    host: str
    owner: str
    slug: str
    description: str
    private: bool

    @property
    def key(self):
        return f"{self.host}/{self.owner}/{self.slug}".lower()


class RepositoryHost:
    domain: str
    name: str
    owner_noun: str
    default_api_base: str
    credentials_hint: str
    rejected_credentials_hint = ""

    def __init__(self, api_authorization=None):
        self._given_api_authorization = api_authorization

    def web_url(self, owner, repository):
        return f"https://{self.domain}/{owner}/{repository}"

    def clone_url(self, owner, repository):
        base = os.environ.get(f"{self._variable_prefix}_GIT_BASE", f"https://{self.domain}").rstrip("/")
        return f"{base}/{owner}/{repository}.git"

    def permalink_template(self, owner, repository, commit):
        raise NotImplementedError

    def clone_credentials(self):
        raise NotImplementedError

    def has_api_credentials(self):
        return bool(self._available_api_authorization)

    def owners(self):
        return [Owner(**item) for item in self._list_in_child_process("owners")]

    def repositories(self, owner):
        return [Repository(**item) for item in self._list_in_child_process("repositories", owner)]

    def _find_api_authorization(self):
        raise NotImplementedError

    def _list_owners(self):
        raise NotImplementedError

    def _list_repositories(self, owner):
        raise NotImplementedError

    def _next_page_url(self, page, link_header):
        raise NotImplementedError

    def _page_values(self, page):
        raise NotImplementedError

    @property
    def _variable_prefix(self):
        return f"DELPHI_CODE_{self.name.upper()}"

    @property
    def _api_base(self):
        return os.environ.get(f"{self._variable_prefix}_API_BASE", self.default_api_base).rstrip("/")

    @cached_property
    def _available_api_authorization(self):
        return self._given_api_authorization or self._find_api_authorization()

    @property
    def _api_authorization(self):
        authorization = self._available_api_authorization
        if not authorization:
            raise Failure(
                "remote_auth_missing",
                f"Listing {self.name} repositories needs credentials: {self.credentials_hint}",
                ExitCode.RUNTIME_ASSETS,
            )
        return authorization

    def _repository(self, owner, slug, description, private):
        return Repository(self.domain, owner, slug, description or "", bool(private))

    def _fetch_pages(self, url):
        values = []
        while url:
            page, link_header = self._fetch_json(url)
            values.extend(self._page_values(page))
            url = self._next_page_url(page, link_header)
        return values

    def _fetch_json(self, url):
        headers = {
            "Authorization": self._given_api_authorization,
            "Accept": "application/json",
            "User-Agent": "delphi-code",
        }
        tls = _verified_tls() if url.startswith("https:") else None
        with urlopen(Request(url, headers=headers), timeout=REQUEST_TIMEOUT_SECONDS, context=tls) as response:
            return json.load(response), (response.headers.get("Link") if response.headers else None) or ""

    def _list_in_child_process(self, listing, *arguments) -> list[dict]:
        environment = dict(os.environ, DELPHI_CODE_API_AUTHORIZATION=self._api_authorization)
        try:
            result = subprocess.run(
                [sys.executable, __file__, self.domain, listing, *arguments],
                capture_output=True,
                text=True,
                timeout=LISTING_TIMEOUT_SECONDS,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise Failure(
                "remote_unavailable",
                f"{self.name} did not answer within {LISTING_TIMEOUT_SECONDS} seconds",
                ExitCode.OPERATION,
            ) from exc
        payload: Any
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            payload = {"error": {"status": None, "message": result.stderr.strip() or f"{self.name} API request failed"}}
        if result.returncode == 0:
            return payload
        error = payload.get("error", {})
        if error.get("status") in (401, 403):
            raise Failure(
                "remote_auth_failed",
                f"{self.name} refused the credentials: {error['message']}. {self.rejected_credentials_hint or self.credentials_hint}",
                ExitCode.OPERATION,
            )
        raise Failure(
            "remote_unavailable", f"{self.name} API request failed: {error.get('message')}", ExitCode.OPERATION
        )


class Bitbucket(RepositoryHost):
    domain = "bitbucket.org"
    name = "Bitbucket"
    owner_noun = "workspace"
    default_api_base = "https://api.bitbucket.org/2.0"
    credentials_hint = "set BITBUCKET_USERNAME and BITBUCKET_APP_PASSWORD, or store git credentials for bitbucket.org"
    rejected_credentials_hint = "With an API token, set BITBUCKET_EMAIL to your Atlassian account email"
    _repository_fields = "next,values.slug,values.description,values.is_private"

    def permalink_template(self, owner, repository, commit):
        return f"{self.web_url(owner, repository)}/src/{commit}/{{path}}#lines-{{start}}:{{end}}"

    def clone_credentials(self):
        username, password = os.environ.get("BITBUCKET_USERNAME"), os.environ.get("BITBUCKET_APP_PASSWORD")
        return (username, password) if username and password else None

    def _find_api_authorization(self):
        username = os.environ.get("BITBUCKET_EMAIL") or os.environ.get("BITBUCKET_USERNAME")
        password = os.environ.get("BITBUCKET_APP_PASSWORD")
        credentials = (username, password) if username and password else _git_stored_credentials(self.domain)
        return "Basic " + base64.b64encode(":".join(credentials).encode()).decode() if credentials else None

    def _workspaces_url(self):
        return f"{self._api_base}/user/workspaces?pagelen={PAGE_LENGTH}"

    def _legacy_workspaces_url(self):
        return f"{self._api_base}/user/permissions/workspaces?pagelen={PAGE_LENGTH}"

    def _repositories_url(self, workspace):
        query = urlencode({"role": "member", "pagelen": PAGE_LENGTH, "sort": "slug", "fields": self._repository_fields})
        return f"{self._api_base}/repositories/{quote(workspace)}?{query}"

    def _list_owners(self):
        try:
            memberships = self._fetch_pages(self._workspaces_url())
        except HTTPError as exc:
            exc.close()
            if exc.code not in (404, 410):
                raise
            memberships = self._fetch_pages(self._legacy_workspaces_url())
        names_by_slug = {}
        for membership in memberships:
            workspace = membership.get("workspace", membership)
            names_by_slug[workspace["slug"]] = workspace.get("name") or workspace["slug"]
        return [Owner(slug, name) for slug, name in sorted(names_by_slug.items())]

    def _list_repositories(self, workspace):
        return [
            self._repository(workspace, item["slug"], item.get("description"), item.get("is_private"))
            for item in self._fetch_pages(self._repositories_url(workspace))
        ]

    def _next_page_url(self, page, link_header):
        return page.get("next")

    def _page_values(self, page):
        return page.get("values", [])


class GitHub(RepositoryHost):
    domain = "github.com"
    name = "GitHub"
    owner_noun = "account"
    default_api_base = "https://api.github.com"
    credentials_hint = "set GITHUB_TOKEN, sign in with gh auth login, or store git credentials for github.com"
    _next_link = re.compile(r'<([^>]+)>;\s*rel="next"')

    def permalink_template(self, owner, repository, commit):
        return f"{self.web_url(owner, repository)}/blob/{commit}/{{path}}#L{{start}}-L{{end}}"

    def clone_credentials(self):
        token = _github_environment_token()
        return ("x-access-token", token) if token else None

    def _find_api_authorization(self):
        token = _github_environment_token() or _gh_token(self.domain)
        if not token:
            stored = _git_stored_credentials(self.domain)
            token = stored[1] if stored else None
        return f"Bearer {token}" if token else None

    def owners(self):
        owners = sorted({repository.owner for repository in self._accessible_repositories}, key=str.lower)
        return [Owner(owner, owner) for owner in owners]

    def repositories(self, owner):
        return [repository for repository in self._accessible_repositories if repository.owner == owner]

    @cached_property
    def _accessible_repositories(self):
        return [Repository(**item) for item in self._list_in_child_process("repositories")]

    def _repositories_url(self):
        query = urlencode(
            {"per_page": PAGE_LENGTH, "sort": "full_name", "affiliation": "owner,collaborator,organization_member"}
        )
        return f"{self._api_base}/user/repos?{query}"

    def _list_repositories(self, owner=None):
        repositories = [
            self._repository(item["owner"]["login"], item["name"], item.get("description"), item.get("private"))
            for item in self._fetch_pages(self._repositories_url())
        ]
        return [repository for repository in repositories if owner in (None, repository.owner)]

    def _next_page_url(self, page, link_header):
        match = self._next_link.search(link_header)
        return match.group(1) if match else None

    def _page_values(self, page):
        return page


HOSTS = (Bitbucket, GitHub)
HOSTS_BY_DOMAIN = {host.domain: host for host in HOSTS}


def configured_hosts():
    return [host for host in (host_type() for host_type in HOSTS) if host.has_api_credentials()]


def _github_environment_token():
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def _verified_tls():
    import certifi

    return ssl.create_default_context(cafile=certifi.where())


def _gh_token(domain):
    try:
        result = subprocess.run(
            ["gh", "auth", "token", "--hostname", domain], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def _git_stored_credentials(domain):
    environment = {name: value for name, value in os.environ.items() if name not in {"GIT_ASKPASS", "SSH_ASKPASS"}}
    environment["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(
            ["git", "credential", "fill"],
            input=f"protocol=https\nhost={domain}\n\n",
            capture_output=True,
            text=True,
            timeout=30,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if result.returncode or not fields.get("username") or not fields.get("password"):
        return None
    return fields["username"], fields["password"]


def main(arguments):
    domain, listing, *listing_arguments = arguments
    host = HOSTS_BY_DOMAIN[domain](os.environ["DELPHI_CODE_API_AUTHORIZATION"])
    try:
        items = host._list_owners() if listing == "owners" else host._list_repositories(*listing_arguments)
    except HTTPError as exc:
        exc.close()
        print(json.dumps({"error": {"status": exc.code, "message": f"HTTP {exc.code} {exc.reason}"}}))
        return 1
    except (URLError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": {"status": None, "message": str(exc)}}))
        return 1
    print(json.dumps([item._asdict() for item in items]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
