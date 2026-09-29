import base64
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.parse import urlsplit

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from delphi_code import hosts
from delphi_code.hosts import Bitbucket, GitHub, Owner, Repository, configured_hosts
from delphi_code.errors import Failure
from delphi_code.picker import RepositoryPicker, TrackingChanges

CREDENTIAL_VARIABLES = ("BITBUCKET_USERNAME", "BITBUCKET_APP_PASSWORD", "BITBUCKET_EMAIL", "GITHUB_TOKEN", "GH_TOKEN")


def answering(value):
    return MagicMock(return_value=MagicMock(ask=MagicMock(return_value=value)))


class FakeHost:
    owner_noun = "workspace"

    def __init__(self, owners, repositories, name="Bitbucket"):
        self._owners, self._repositories, self.name = owners, repositories, name

    def owners(self):
        return self._owners

    def repositories(self, owner):
        return [repository for repository in self._repositories if repository.owner == owner]


ACME = [Repository("bitbucket.org", "acme", "api", "Public API", False), Repository("bitbucket.org", "acme", "billing", "", True),
        Repository("bitbucket.org", "acme", "web", "", False)]
OCTO = [Repository("github.com", "octo", "tools", "", False)]


def picker(owners, repositories=ACME, terminal=None):
    return RepositoryPicker([FakeHost(owners, repositories)], terminal={} if terminal is None else terminal)


class Picking(unittest.TestCase):
    def test_changes_only_touch_the_listed_owner(self):
        tracked = {"bitbucket.org/acme/api", "bitbucket.org/acme/web", "bitbucket.org/other/tool"}
        with patch("questionary.checkbox", answering(["bitbucket.org/acme/api", "bitbucket.org/acme/billing"])), patch("questionary.confirm", answering(True)):
            changes = picker([Owner("acme", "Acme")]).choose(tracked)
        self.assertEqual(changes, TrackingChanges(["bitbucket.org/acme/billing"], ["bitbucket.org/acme/web"]))

    def test_single_host_and_owner_skip_their_questions(self):
        with patch("questionary.select") as select, patch("questionary.checkbox", answering(["bitbucket.org/acme/web"])) as checkbox, \
                patch("questionary.confirm", answering(True)):
            changes = picker([Owner("acme", "Acme")]).choose({"bitbucket.org/acme/api"})
        select.assert_not_called()
        self.assertEqual(changes, TrackingChanges(["bitbucket.org/acme/web"], ["bitbucket.org/acme/api"]))
        choices = checkbox.call_args.kwargs["choices"]
        self.assertEqual([(choice.title, choice.value, choice.checked) for choice in choices],
                         [("api  Public API", "bitbucket.org/acme/api", True), ("billing (private)", "bitbucket.org/acme/billing", False),
                          ("web", "bitbucket.org/acme/web", False)])

    def test_several_hosts_are_offered_first(self):
        bitbucket = FakeHost([Owner("acme", "Acme")], ACME)
        github = FakeHost([Owner("octo", "octo")], OCTO, name="GitHub")
        with patch("questionary.select", answering(github)) as select, patch("questionary.checkbox", answering(["github.com/octo/tools"])), \
                patch("questionary.confirm", answering(True)):
            changes = RepositoryPicker([bitbucket, github], terminal={}).choose(set())
        self.assertEqual([choice.title for choice in select.call_args.kwargs["choices"]], ["Bitbucket", "GitHub"])
        self.assertEqual(changes, TrackingChanges(["github.com/octo/tools"], []))

    def test_cancelling_any_question_changes_nothing(self):
        two_owners = picker([Owner("acme", "Acme"), Owner("other", "Other")])
        with patch("questionary.select", answering(None)):
            self.assertIsNone(two_owners.choose(set()))
        with patch("questionary.select", answering("acme")), patch("questionary.checkbox", answering(None)):
            self.assertIsNone(two_owners.choose(set()))
        with patch("questionary.select", answering("acme")), patch("questionary.checkbox", answering(["bitbucket.org/acme/api"])), \
                patch("questionary.confirm", answering(False)):
            self.assertIsNone(two_owners.choose(set()))
        two_hosts = RepositoryPicker([FakeHost([], []), FakeHost([], [], name="GitHub")], terminal={})
        with patch("questionary.select", answering(None)):
            self.assertIsNone(two_hosts.choose(set()))

    def test_unchanged_selection_needs_no_confirmation(self):
        with patch("questionary.checkbox", answering(["bitbucket.org/acme/api"])), patch("questionary.confirm") as confirm:
            self.assertEqual(picker([Owner("acme", "Acme")]).choose({"bitbucket.org/acme/api"}), TrackingChanges([], []))
        confirm.assert_not_called()

    def test_empty_owner_and_account(self):
        with self.assertRaises(Failure) as raised:
            picker([], []).choose(set())
        self.assertEqual(raised.exception.code, "remote_empty")
        with self.assertRaises(Failure) as raised:
            picker([Owner("acme", "Acme")], []).choose(set())
        self.assertEqual(raised.exception.code, "remote_empty")

    def test_real_checkbox_reads_keys_from_the_given_terminal(self):
        with create_pipe_input() as keys:
            keys.send_text("bill")
            keys.send_text(" ")
            keys.send_text("\r")
            with patch("questionary.confirm", answering(True)):
                changes = picker([Owner("acme", "Acme")], terminal={"input": keys, "output": DummyOutput()}).choose(set())
        self.assertEqual(changes, TrackingChanges(["bitbucket.org/acme/billing"], []))

    def test_long_descriptions_are_shortened_to_their_first_line(self):
        long = Repository("bitbucket.org", "acme", "x", "a" * 80 + "\nsecond line", False)
        with patch("questionary.checkbox", answering(None)) as checkbox:
            picker([Owner("acme", "Acme")], [long]).choose(set())
        self.assertEqual(checkbox.call_args.kwargs["choices"][0].title, "x  " + "a" * 59 + "…")

    def test_needs_a_terminal_before_asking_for_credentials(self):
        with patch.object(sys.stdin, "isatty", return_value=False), patch("delphi_code.hosts.configured_hosts") as configured, \
                self.assertRaises(Failure) as raised:
            RepositoryPicker()
        self.assertEqual(raised.exception.code, "usage")
        configured.assert_not_called()

    def test_needs_credentials_for_some_host(self):
        with patch("delphi_code.hosts.configured_hosts", return_value=[]), self.assertRaises(Failure) as raised:
            RepositoryPicker(terminal={})
        self.assertEqual(raised.exception.code, "remote_auth_missing")


class FakeResponse(io.BytesIO):
    def __init__(self, content, link=""):
        super().__init__(json.dumps(content).encode())
        self.headers = {"Link": link}


class HostListingScript(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"DELPHI_CODE_BITBUCKET_API_BASE": "http://bitbucket.test/2.0", "DELPHI_CODE_GITHUB_API_BASE": "http://github.test",
                                                  "DELPHI_CODE_API_AUTHORIZATION": "Basic bWU6cHc="}))

    def serve(self, pages_by_path):
        requests = []

        def urlopen(request, timeout, context):
            requests.append(request)
            page = pages_by_path[urlsplit(request.full_url).path]
            if isinstance(page, int):
                raise HTTPError(request.full_url, page, "Refused", {}, io.BytesIO())
            return page if isinstance(page, FakeResponse) else FakeResponse(page)

        self.enterContext(patch.object(hosts, "urlopen", side_effect=urlopen))
        return requests

    def listed(self, *arguments):
        output = io.StringIO()
        with patch("sys.stdout", output):
            code = hosts.main(list(arguments))
        return code, json.loads(output.getvalue())

    def test_bitbucket_pages_are_followed_and_authorization_sent(self):
        requests = self.serve({
            "/2.0/repositories/acme": {"values": [{"slug": "api", "is_private": True}], "next": "http://bitbucket.test/2.0/page-2"},
            "/2.0/page-2": {"values": [{"slug": "web", "description": "Site"}]},
        })
        code, repositories = self.listed("bitbucket.org", "repositories", "acme")
        self.assertEqual(code, 0)
        self.assertEqual([Repository(**item) for item in repositories],
                         [Repository("bitbucket.org", "acme", "api", "", True), Repository("bitbucket.org", "acme", "web", "Site", False)])
        self.assertEqual(requests[0].get_header("Authorization"), "Basic bWU6cHc=")

    def test_bitbucket_workspaces_fall_back_to_the_permissions_endpoint(self):
        self.serve({
            "/2.0/user/workspaces": 410,
            "/2.0/user/permissions/workspaces": {"values": [{"workspace": {"slug": "zeta", "name": "Zeta"}}, {"workspace": {"slug": "acme"}}]},
        })
        self.assertEqual(self.listed("bitbucket.org", "owners"), (0, [{"slug": "acme", "name": "acme"}, {"slug": "zeta", "name": "Zeta"}]))

    def test_github_pages_follow_link_headers(self):
        self.serve({
            "/user/repos": FakeResponse([{"owner": {"login": "octo"}, "name": "tools", "private": True}],
                                        '<http://github.test/page-2>; rel="next", <http://github.test/page-2>; rel="last"'),
            "/page-2": FakeResponse([{"owner": {"login": "me"}, "name": "dotfiles", "description": "Mine"}]),
        })
        code, repositories = self.listed("github.com", "repositories")
        self.assertEqual([Repository(**item) for item in repositories],
                         [Repository("github.com", "octo", "tools", "", True), Repository("github.com", "me", "dotfiles", "Mine", False)])

    def test_http_errors_are_reported_as_json(self):
        self.serve({"/2.0/user/workspaces": 401})
        self.assertEqual(self.listed("bitbucket.org", "owners"), (1, {"error": {"status": 401, "message": "HTTP 401 Refused"}}))


class Hosts(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        for name in CREDENTIAL_VARIABLES:
            self.enterContext(patch.dict(os.environ))
            os.environ.pop(name, None)

    def test_listing_runs_in_a_child_process_without_the_network_guard(self):
        with patch.dict(os.environ, {"DELPHI_CODE_BITBUCKET_API_BASE": (self.root / "missing").as_uri()}):
            with self.assertRaises(Failure) as raised:
                Bitbucket("Basic x").owners()
        self.assertEqual(raised.exception.code, "remote_unavailable")
        with patch.dict(os.environ, {"DELPHI_CODE_BITBUCKET_API_BASE": "http://127.0.0.1:9"}):
            with self.assertRaises(Failure) as raised:
                Bitbucket("Basic x").owners()
        self.assertIn("refused", str(raised.exception).lower())

    def test_github_groups_repositories_by_owner(self):
        listed = [{"host": "github.com", "owner": "Octo", "slug": "tools", "description": "", "private": False},
                  {"host": "github.com", "owner": "me", "slug": "dotfiles", "description": "", "private": False},
                  {"host": "github.com", "owner": "Octo", "slug": "api", "description": "", "private": True}]
        answered = subprocess.CompletedProcess([], 0, json.dumps(listed), "")
        with patch("delphi_code.hosts.subprocess.run", return_value=answered) as run:
            github = GitHub("Bearer x")
            self.assertEqual(github.owners(), [Owner("me", "me"), Owner("Octo", "Octo")])
            self.assertEqual([repository.key for repository in github.repositories("Octo")], ["github.com/octo/tools", "github.com/octo/api"])
        run.assert_called_once()

    def test_rejected_credentials(self):
        rejected = subprocess.CompletedProcess([], 1, json.dumps({"error": {"status": 401, "message": "HTTP 401 Unauthorized"}}), "")
        with patch("delphi_code.hosts.subprocess.run", return_value=rejected), self.assertRaises(Failure) as raised:
            Bitbucket("Basic x").owners()
        self.assertEqual(raised.exception.code, "remote_auth_failed")
        self.assertIn("BITBUCKET_EMAIL", str(raised.exception))

    def authorization_sent(self, host):
        self.assertTrue(host.has_api_credentials())
        answered = subprocess.CompletedProcess([], 0, "[]", "")
        with patch("delphi_code.hosts.subprocess.run", return_value=answered) as run:
            host.owners()
        return run.call_args.kwargs["env"]["DELPHI_CODE_API_AUTHORIZATION"]

    def isolate_git_and_gh(self, gh_token=None):
        store = self.root / "git-credentials"
        config = self.root / "gitconfig"
        config.write_text(f"[credential]\n\thelper = store --file {store}\n")
        tools = self.root / "bin"
        tools.mkdir(exist_ok=True)
        if not (tools / "git").exists():
            (tools / "git").symlink_to("/usr/bin/git")
        if gh_token:
            (tools / "gh").write_text(f"#!/bin/sh\necho {gh_token}\n")
            (tools / "gh").chmod(0o755)
        self.enterContext(patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(self.root), "PATH": str(tools)}))
        return store

    def test_bitbucket_credentials_prefer_environment_then_git(self):
        store = self.isolate_git_and_gh()
        self.assertFalse(Bitbucket().has_api_credentials())
        with self.assertRaises(Failure) as raised:
            Bitbucket().owners()
        self.assertEqual(raised.exception.code, "remote_auth_missing")
        store.write_text("https://stored-user:stored-pass@bitbucket.org\n")
        basic = lambda text: "Basic " + base64.b64encode(text.encode()).decode()
        self.assertEqual(self.authorization_sent(Bitbucket()), basic("stored-user:stored-pass"))
        with patch.dict(os.environ, {"BITBUCKET_USERNAME": "me", "BITBUCKET_APP_PASSWORD": "pw"}):
            self.assertEqual(self.authorization_sent(Bitbucket()), basic("me:pw"))
            with patch.dict(os.environ, {"BITBUCKET_EMAIL": "me@example.com"}):
                self.assertEqual(self.authorization_sent(Bitbucket()), basic("me@example.com:pw"))

    def test_github_credentials_prefer_environment_then_gh_then_git(self):
        store = self.isolate_git_and_gh()
        self.assertFalse(GitHub().has_api_credentials())
        store.write_text("https://me:stored-token@github.com\n")
        self.assertEqual(self.authorization_sent(GitHub()), "Bearer stored-token")
        self.isolate_git_and_gh(gh_token="gh-token")
        self.assertEqual(self.authorization_sent(GitHub()), "Bearer gh-token")
        with patch.dict(os.environ, {"GITHUB_TOKEN": "env-token"}):
            self.assertEqual(self.authorization_sent(GitHub()), "Bearer env-token")

    def test_only_configured_hosts_are_offered(self):
        self.isolate_git_and_gh()
        self.assertEqual(configured_hosts(), [])
        with patch.dict(os.environ, {"GITHUB_TOKEN": "token"}):
            self.assertEqual([host.name for host in configured_hosts()], ["GitHub"])
            with patch.dict(os.environ, {"BITBUCKET_USERNAME": "me", "BITBUCKET_APP_PASSWORD": "pw"}):
                self.assertEqual([host.name for host in configured_hosts()], ["Bitbucket", "GitHub"])
