import base64
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import NamedTuple
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.parse import urlsplit

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from delphi_code.controllers import indexing as indexing_controller
from delphi_code.controllers import repository_picking
from delphi_code.domain.errors import ExitCode, Failure
from delphi_code.domain.tracking_changes import TrackingChanges
from delphi_code.infrastructure import hosts
from delphi_code.infrastructure.hosts import Bitbucket, GitHub, Owner, Repository, configured_hosts
from delphi_code.services.progress import SILENT, Progress
from delphi_code.ui.prompts import TerminalPrompts
from delphi_code.ui.repository_list import RepositoryList, repository_title

CREDENTIAL_VARIABLES = ("BITBUCKET_USERNAME", "BITBUCKET_APP_PASSWORD", "BITBUCKET_EMAIL", "GITHUB_TOKEN", "GH_TOKEN")


def answering(value):
    return MagicMock(return_value=MagicMock(ask=MagicMock(return_value=value)))


def choosing(keys):
    return patch("delphi_code.ui.prompts.ask_repositories", return_value=None if keys is None else set(keys))


class FakeHost:
    owner_noun = "workspace"

    def __init__(self, owners, repositories, name="Bitbucket", suggested_owner=None):
        self._owners, self._repositories, self.name = owners, repositories, name
        self._suggested_owner = suggested_owner

    def owners(self):
        if isinstance(self._owners, Failure):
            raise self._owners
        return self._owners

    def suggested_owner(self):
        return self._suggested_owner

    def repositories(self, owner):
        return [repository for repository in self._repositories if repository.owner == owner]


ACME = [
    Repository("bitbucket.org", "acme", "api", "Public API", False),
    Repository("bitbucket.org", "acme", "billing", "", True),
    Repository("bitbucket.org", "acme", "web", "", False),
]
OCTO = [Repository("github.com", "octo", "tools", "", False)]


def pick(hosts, tracked_keys, terminal=None):
    prompts = TerminalPrompts({} if terminal is None else terminal)
    return repository_picking.pick_tracking_changes(hosts, tracked_keys, prompts, SILENT)


def pick_from(owners, tracked_keys, repositories=ACME, terminal=None):
    return pick([FakeHost(owners, repositories)], tracked_keys, terminal)


class Picking(unittest.TestCase):
    def test_changes_only_touch_the_listed_owner(self):
        tracked = {"bitbucket.org/acme/api", "bitbucket.org/acme/web", "bitbucket.org/other/tool"}
        with (
            choosing(["bitbucket.org/acme/api", "bitbucket.org/acme/billing"]),
            patch("questionary.confirm", answering(True)),
        ):
            changes = pick_from([Owner("acme", "Acme")], tracked)
        self.assertEqual(changes, TrackingChanges(["bitbucket.org/acme/billing"], ["bitbucket.org/acme/web"]))

    def test_single_host_and_owner_skip_their_questions(self):
        with (
            patch("questionary.select") as select,
            choosing(["bitbucket.org/acme/web"]) as ask,
            patch("questionary.confirm", answering(True)),
        ):
            changes = pick_from([Owner("acme", "Acme")], {"bitbucket.org/acme/api"})
        select.assert_not_called()
        self.assertEqual(changes, TrackingChanges(["bitbucket.org/acme/web"], ["bitbucket.org/acme/api"]))
        self.assertEqual(ask.call_args.args[:3], ("Repositories to index in acme", ACME, {"bitbucket.org/acme/api"}))

    def test_several_hosts_are_offered_first(self):
        bitbucket = FakeHost([Owner("acme", "Acme")], ACME)
        github = FakeHost([Owner("octo", "octo")], OCTO, name="GitHub")
        with (
            patch("questionary.select", answering(github)) as select,
            choosing(["github.com/octo/tools"]),
            patch("questionary.confirm", answering(True)),
        ):
            changes = pick([bitbucket, github], set())
        self.assertEqual([choice.title for choice in select.call_args.kwargs["choices"]], ["Bitbucket", "GitHub"])
        self.assertEqual(changes, TrackingChanges(["github.com/octo/tools"], []))

    def test_cancelling_any_question_changes_nothing(self):
        two_owners = [FakeHost([Owner("acme", "Acme"), Owner("other", "Other")], ACME)]
        with patch("questionary.select", answering(None)):
            self.assertIsNone(pick(two_owners, set()))
        with patch("questionary.select", answering("acme")), choosing(None):
            self.assertIsNone(pick(two_owners, set()))
        with (
            patch("questionary.select", answering("acme")),
            choosing(["bitbucket.org/acme/api"]),
            patch("questionary.confirm", answering(False)),
        ):
            self.assertIsNone(pick(two_owners, set()))
        two_hosts = [FakeHost([], []), FakeHost([], [], name="GitHub")]
        with patch("questionary.select", answering(None)):
            self.assertIsNone(pick(two_hosts, set()))

    def test_owner_is_typed_when_listing_owners_is_forbidden(self):
        forbidden = Failure("remote_permission_denied", "lacks read:workspace:bitbucket", ExitCode.OPERATION)
        host = FakeHost(forbidden, ACME, suggested_owner="acme")
        with (
            patch("questionary.text", answering("  acme ")) as text,
            choosing(["bitbucket.org/acme/api"]),
            patch("questionary.confirm", answering(True)),
        ):
            changes = pick([host], set())
        self.assertEqual(text.call_args.kwargs["default"], "acme")
        self.assertEqual(changes, TrackingChanges(["bitbucket.org/acme/api"], []))
        for cancelled in (None, "   "):
            with patch("questionary.text", answering(cancelled)):
                self.assertIsNone(pick([FakeHost(forbidden, ACME)], set()))

    def test_other_listing_failures_are_not_turned_into_questions(self):
        unavailable = Failure("remote_unavailable", "down", ExitCode.OPERATION)
        with patch("questionary.text") as text, self.assertRaises(Failure) as raised:
            pick([FakeHost(unavailable, ACME)], set())
        self.assertEqual(raised.exception.code, "remote_unavailable")
        text.assert_not_called()

    def test_unchanged_selection_needs_no_confirmation(self):
        with (
            choosing(["bitbucket.org/acme/api"]),
            patch("questionary.confirm") as confirm,
        ):
            self.assertEqual(pick_from([Owner("acme", "Acme")], {"bitbucket.org/acme/api"}), TrackingChanges([], []))
        confirm.assert_not_called()

    def test_empty_owner_and_account(self):
        with self.assertRaises(Failure) as raised:
            pick_from([], [], set())
        self.assertEqual(raised.exception.code, "remote_empty")
        with self.assertRaises(Failure) as raised:
            pick_from([Owner("acme", "Acme")], [], set())
        self.assertEqual(raised.exception.code, "remote_empty")

    def test_real_list_reads_keys_from_the_given_terminal(self):
        with create_pipe_input() as keys:
            keys.send_text("bill")
            keys.send_text(" ")
            keys.send_text("\r")
            with patch("questionary.confirm", answering(True)):
                changes = pick_from([Owner("acme", "Acme")], set(), terminal={"input": keys, "output": DummyOutput()})
        self.assertEqual(changes, TrackingChanges(["bitbucket.org/acme/billing"], []))

    def test_real_list_cancels_on_escape(self):
        with create_pipe_input() as keys:
            keys.send_text(" \x1b")
            self.assertIsNone(
                pick_from([Owner("acme", "Acme")], set(), terminal={"input": keys, "output": DummyOutput()})
            )

    def add_picked(self, prompts):
        return indexing_controller.add([], None, None, "", True, SILENT, prompts).data

    def test_needs_a_terminal_before_asking_for_credentials(self):
        with (
            patch.object(sys.stdin, "isatty", return_value=False),
            patch("delphi_code.infrastructure.hosts.configured_hosts") as configured,
            self.assertRaises(Failure) as raised,
        ):
            self.add_picked(TerminalPrompts())
        self.assertEqual(raised.exception.code, "usage")
        configured.assert_not_called()

    def test_needs_credentials_for_some_host(self):
        with (
            patch("delphi_code.infrastructure.hosts.configured_hosts", return_value=[]),
            self.assertRaises(Failure) as raised,
        ):
            self.add_picked(TerminalPrompts({}))
        self.assertEqual(raised.exception.code, "remote_auth_missing")

    def test_listing_progress_ends_before_each_question(self):
        forbidden = Failure("remote_permission_denied", "lacks read:workspace:bitbucket", ExitCode.OPERATION)
        progress = MagicMock(spec=Progress)
        prompts = MagicMock(spec=TerminalPrompts)
        prompts.type_owner.side_effect = lambda host: progress.mock_calls.append("type_owner") or "acme"
        prompts.choose_repositories.return_value = None
        repository_picking.pick_tracking_changes([FakeHost(forbidden, ACME)], set(), prompts, progress)
        self.assertEqual(
            [str(event) for event in progress.mock_calls],
            [
                "call.owners_listing_started('Bitbucket', 'workspace')",
                "call.listing_finished()",
                "type_owner",
                "call.repositories_listing_started('Bitbucket', 'acme')",
                "call.listing_finished()",
            ],
        )


def numbered_repositories(count):
    return [Repository("bitbucket.org", "acme", f"repo-{number:04}", "", False) for number in range(count)]


class Listing(unittest.TestCase):
    def test_only_one_page_is_visible(self):
        listing = RepositoryList(numbered_repositories(3000), set(), page_size=20)
        self.assertEqual((len(listing.visible()), listing.page_count), (20, 150))
        listing.turn_page(1)
        listing.move(-1)
        self.assertEqual((listing.page, listing.cursor), (0, 19))
        listing.go_to_last()
        self.assertEqual(
            [repository.slug for repository, _, _ in listing.visible()], [f"repo-{n}" for n in range(2980, 3000)]
        )

    def test_filter_ranks_slug_matches_before_description_matches(self):
        listing = RepositoryList(
            [
                Repository("bitbucket.org", "acme", "web", "Talks to the billing API", False),
                Repository("bitbucket.org", "acme", "Billing", "", True),
                Repository("bitbucket.org", "acme", "api", "", False),
            ],
            set(),
        )
        listing.type("BILL")
        self.assertEqual([repository.slug for repository in listing.matches], ["Billing", "web"])
        listing.erase()
        listing.type("x")
        self.assertEqual(listing.matches, [])
        listing.clear()
        self.assertEqual(len(listing.matches), 3)

    def test_toggling_all_matches_and_showing_only_selected(self):
        listing = RepositoryList(numbered_repositories(30), {"bitbucket.org/acme/repo-0001"})
        listing.type("repo-001")
        listing.toggle_all_matches()
        self.assertEqual(len(listing.chosen_keys), 11)
        listing.clear()
        listing.toggle_only_chosen()
        self.assertEqual(len(listing.matches), 11)
        listing.toggle_current()
        self.assertNotIn("bitbucket.org/acme/repo-0001", listing.chosen_keys)
        listing.type("repo-001")
        listing.toggle_all_matches()
        self.assertEqual(listing.chosen_keys, set())

    def test_long_descriptions_are_shortened_to_their_first_line(self):
        long = Repository("bitbucket.org", "acme", "x", "a" * 80 + "\nsecond line", False)
        self.assertEqual(repository_title(long), "x  " + "a" * 59 + "…")
        self.assertEqual(repository_title(ACME[1]), "billing (private)")


class FakeResponse(io.BytesIO):
    def __init__(self, content, link=""):
        super().__init__(json.dumps(content).encode())
        self.headers = {"Link": link}


class HttpFailure(NamedTuple):
    status: int
    body: object = None


BITBUCKET_SCOPE_ERROR = {
    "type": "error",
    "error": {
        "message": "Your credentials lack one or more required privilege scopes.",
        "detail": {"granted": ["repository"], "required": ["account"]},
    },
}


class HostListingScript(unittest.TestCase):
    def setUp(self):
        self.enterContext(
            patch.dict(
                os.environ,
                {
                    "DELPHI_CODE_BITBUCKET_API_BASE": "http://bitbucket.test/2.0",
                    "DELPHI_CODE_GITHUB_API_BASE": "http://github.test",
                    "DELPHI_CODE_API_AUTHORIZATION": "Basic bWU6cHc=",
                },
            )
        )

    def serve(self, pages_by_path):
        requests = []

        def urlopen(request, timeout, context):
            requests.append(request)
            page = pages_by_path[urlsplit(request.full_url).path]
            if isinstance(page, HttpFailure):
                raise HTTPError(
                    request.full_url, page.status, "Refused", {}, io.BytesIO(json.dumps(page.body).encode())
                )
            return page if isinstance(page, FakeResponse) else FakeResponse(page)

        self.enterContext(patch.object(hosts, "urlopen", side_effect=urlopen))
        return requests

    def listed(self, *arguments):
        output = io.StringIO()
        with patch("sys.stdout", output):
            code = hosts.main(list(arguments))
        return code, json.loads(output.getvalue())

    def test_bitbucket_pages_are_followed_and_authorization_sent(self):
        requests = self.serve(
            {
                "/2.0/repositories/acme": {
                    "values": [{"slug": "api", "is_private": True}],
                    "next": "http://bitbucket.test/2.0/page-2",
                },
                "/2.0/page-2": {"values": [{"slug": "web", "description": "Site"}]},
            }
        )
        code, repositories = self.listed("bitbucket.org", "repositories", "acme")
        self.assertEqual(code, 0)
        self.assertEqual(
            [Repository(**item) for item in repositories],
            [
                Repository("bitbucket.org", "acme", "api", "", True),
                Repository("bitbucket.org", "acme", "web", "Site", False),
            ],
        )
        self.assertEqual(requests[0].get_header("Authorization"), "Basic bWU6cHc=")

    def test_github_pages_follow_link_headers(self):
        self.serve(
            {
                "/user/repos": FakeResponse(
                    [{"owner": {"login": "octo"}, "name": "tools", "private": True}],
                    '<http://github.test/page-2>; rel="next", <http://github.test/page-2>; rel="last"',
                ),
                "/page-2": FakeResponse([{"owner": {"login": "me"}, "name": "dotfiles", "description": "Mine"}]),
            }
        )
        _, repositories = self.listed("github.com", "repositories")
        self.assertEqual(
            [Repository(**item) for item in repositories],
            [
                Repository("github.com", "octo", "tools", "", True),
                Repository("github.com", "me", "dotfiles", "Mine", False),
            ],
        )

    def test_http_errors_are_reported_as_json(self):
        self.serve({"/2.0/user/workspaces": HttpFailure(401)})
        self.assertEqual(
            self.listed("bitbucket.org", "owners"),
            (1, {"error": {"status": 401, "path": "/2.0/user/workspaces", "message": "HTTP 401 Refused"}}),
        )

    def test_bitbucket_scope_errors_name_the_missing_scopes(self):
        self.serve(
            {
                "/2.0/user/workspaces": HttpFailure(403, BITBUCKET_SCOPE_ERROR),
            }
        )
        code, output = self.listed("bitbucket.org", "owners")
        self.assertEqual((code, output["error"]["status"], output["error"]["path"]), (1, 403, "/2.0/user/workspaces"))
        self.assertEqual(
            output["error"]["message"],
            "HTTP 403 Your credentials lack one or more required privilege scopes. (granted: repository; required: account)",
        )

    def test_github_error_messages_are_reported(self):
        self.serve({"/user/repos": HttpFailure(403, {"message": "Resource not accessible by personal access token"})})
        _, output = self.listed("github.com", "repositories")
        self.assertEqual(output["error"]["message"], "HTTP 403 Resource not accessible by personal access token")


class Hosts(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        for name in CREDENTIAL_VARIABLES:
            self.enterContext(patch.dict(os.environ))
            os.environ.pop(name, None)

    def test_listing_runs_in_a_child_process_without_the_network_guard(self):
        with (
            patch.dict(os.environ, {"DELPHI_CODE_BITBUCKET_API_BASE": (self.root / "missing").as_uri()}),
            self.assertRaises(Failure) as raised,
        ):
            Bitbucket("Basic x").owners()
        self.assertEqual(raised.exception.code, "remote_unavailable")
        with (
            patch.dict(os.environ, {"DELPHI_CODE_BITBUCKET_API_BASE": "http://127.0.0.1:9"}),
            self.assertRaises(Failure) as raised,
        ):
            Bitbucket("Basic x").owners()
        self.assertIn("refused", str(raised.exception).lower())

    def test_github_groups_repositories_by_owner(self):
        listed = [
            {"host": "github.com", "owner": "Octo", "slug": "tools", "description": "", "private": False},
            {"host": "github.com", "owner": "me", "slug": "dotfiles", "description": "", "private": False},
            {"host": "github.com", "owner": "Octo", "slug": "api", "description": "", "private": True},
        ]
        answered = subprocess.CompletedProcess([], 0, json.dumps(listed), "")
        with patch("delphi_code.infrastructure.hosts.subprocess.run", return_value=answered) as run:
            github = GitHub("Bearer x")
            self.assertEqual(github.owners(), [Owner("me", "me"), Owner("Octo", "Octo")])
            self.assertEqual(
                [repository.key for repository in github.repositories("Octo")],
                ["github.com/octo/tools", "github.com/octo/api"],
            )
        run.assert_called_once()

    def test_rejected_credentials(self):
        rejected = subprocess.CompletedProcess(
            [], 1, json.dumps({"error": {"status": 401, "message": "HTTP 401 Unauthorized"}}), ""
        )
        with (
            patch("delphi_code.infrastructure.hosts.subprocess.run", return_value=rejected),
            self.assertRaises(Failure) as raised,
        ):
            Bitbucket("Basic x").owners()
        self.assertEqual(raised.exception.code, "remote_auth_failed")
        self.assertIn("BITBUCKET_EMAIL", str(raised.exception))

    def test_forbidden_listing_names_the_credentials_used_and_the_scopes_needed(self):
        self.isolate_git_and_gh().write_text("https://stored-user:stored-pass@bitbucket.org\n")
        bitbucket = Bitbucket()
        self.assertTrue(bitbucket.has_api_credentials())
        forbidden = subprocess.CompletedProcess(
            [],
            1,
            json.dumps({"error": {"status": 403, "path": "/2.0/user/workspaces", "message": "HTTP 403 lacks scopes"}}),
            "",
        )
        with (
            patch("delphi_code.infrastructure.hosts.subprocess.run", return_value=forbidden),
            self.assertRaises(Failure) as raised,
        ):
            bitbucket.owners()
        self.assertEqual(raised.exception.code, "remote_permission_denied")
        message = str(raised.exception)
        self.assertIn("accepted the git credentials stored for bitbucket.org but refused /2.0/user/workspaces", message)
        self.assertIn("read:workspace:bitbucket", message)
        self.assertNotIn("BITBUCKET_EMAIL", message)

    def authorization_sent(self, host):
        self.assertTrue(host.has_api_credentials())
        answered = subprocess.CompletedProcess([], 0, "[]", "")
        with patch("delphi_code.infrastructure.hosts.subprocess.run", return_value=answered) as run:
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
        self.enterContext(
            patch.dict(
                os.environ,
                {
                    "GIT_CONFIG_GLOBAL": str(config),
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "HOME": str(self.root),
                    "PATH": str(tools),
                },
            )
        )
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
