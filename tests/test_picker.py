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
from urllib.request import url2pathname

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from delphi_code import bitbucket_api
from delphi_code.bitbucket_catalog import BitbucketCatalog, Repository, Workspace
from delphi_code.model import Failure
from delphi_code.picker import RepositoryPicker, TrackingChanges


def answering(value):
    return MagicMock(return_value=MagicMock(ask=MagicMock(return_value=value)))


class FakeCatalog:
    def __init__(self, workspaces, repositories):
        self._workspaces, self._repositories = workspaces, repositories

    def workspaces(self):
        return self._workspaces

    def repositories(self, workspace):
        return [repository for repository in self._repositories if repository.workspace == workspace]


ACME = [Repository("acme", "api", "Public API", False), Repository("acme", "billing", "", True), Repository("acme", "web", "", False)]


def picker(workspaces, repositories=ACME, terminal=None):
    return RepositoryPicker(FakeCatalog(workspaces, repositories), terminal={} if terminal is None else terminal)


class Picking(unittest.TestCase):
    def test_changes_only_touch_the_listed_workspace(self):
        tracked = {"bitbucket.org/acme/api", "bitbucket.org/acme/web", "bitbucket.org/other/tool"}
        with patch("questionary.checkbox", answering(["bitbucket.org/acme/api", "bitbucket.org/acme/billing"])), patch("questionary.confirm", answering(True)):
            changes = picker([Workspace("acme", "Acme")]).choose(tracked)
        self.assertEqual(changes, TrackingChanges(["bitbucket.org/acme/billing"], ["bitbucket.org/acme/web"]))

    def test_single_workspace_skips_the_workspace_question(self):
        with patch("questionary.select") as select, patch("questionary.checkbox", answering(["bitbucket.org/acme/web"])) as checkbox, \
                patch("questionary.confirm", answering(True)):
            changes = picker([Workspace("acme", "Acme")]).choose({"bitbucket.org/acme/api"})
        select.assert_not_called()
        self.assertEqual(changes, TrackingChanges(["bitbucket.org/acme/web"], ["bitbucket.org/acme/api"]))
        choices = checkbox.call_args.kwargs["choices"]
        self.assertEqual([(choice.title, choice.value, choice.checked) for choice in choices],
                         [("api  Public API", "bitbucket.org/acme/api", True), ("billing (private)", "bitbucket.org/acme/billing", False),
                          ("web", "bitbucket.org/acme/web", False)])

    def test_cancelling_any_question_changes_nothing(self):
        two_workspaces = picker([Workspace("acme", "Acme"), Workspace("other", "Other")])
        with patch("questionary.select", answering(None)):
            self.assertIsNone(two_workspaces.choose(set()))
        with patch("questionary.select", answering("acme")), patch("questionary.checkbox", answering(None)):
            self.assertIsNone(two_workspaces.choose(set()))
        with patch("questionary.select", answering("acme")), patch("questionary.checkbox", answering(["bitbucket.org/acme/api"])), \
                patch("questionary.confirm", answering(False)):
            self.assertIsNone(two_workspaces.choose(set()))

    def test_unchanged_selection_needs_no_confirmation(self):
        with patch("questionary.checkbox", answering(["bitbucket.org/acme/api"])), patch("questionary.confirm") as confirm:
            self.assertEqual(picker([Workspace("acme", "Acme")]).choose({"bitbucket.org/acme/api"}), TrackingChanges([], []))
        confirm.assert_not_called()

    def test_empty_workspace_and_account(self):
        with self.assertRaises(Failure) as raised:
            picker([], []).choose(set())
        self.assertEqual(raised.exception.code, "remote_empty")
        with self.assertRaises(Failure) as raised:
            picker([Workspace("acme", "Acme")], []).choose(set())
        self.assertEqual(raised.exception.code, "remote_empty")

    def test_real_checkbox_reads_keys_from_the_given_terminal(self):
        with create_pipe_input() as keys:
            keys.send_text("bill")
            keys.send_text(" ")
            keys.send_text("\r")
            with patch("questionary.confirm", answering(True)):
                changes = picker([Workspace("acme", "Acme")], terminal={"input": keys, "output": DummyOutput()}).choose(set())
        self.assertEqual(changes, TrackingChanges(["bitbucket.org/acme/billing"], []))

    def test_long_descriptions_are_shortened_to_their_first_line(self):
        long = Repository("acme", "x", "a" * 80 + "\nsecond line", False)
        with patch("questionary.checkbox", answering(None)) as checkbox:
            picker([Workspace("acme", "Acme")], [long]).choose(set())
        self.assertEqual(checkbox.call_args.kwargs["choices"][0].title, "x  " + "a" * 59 + "…")

    def test_needs_a_terminal_before_asking_for_credentials(self):
        with patch.object(sys.stdin, "isatty", return_value=False), patch("delphi_code.bitbucket_catalog.BitbucketCatalog") as catalog, \
                self.assertRaises(Failure) as raised:
            RepositoryPicker()
        self.assertEqual(raised.exception.code, "usage")
        catalog.assert_not_called()


class ApiScript(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {"DELPHI_CODE_BITBUCKET_API_BASE": "https://api.test/2.0",
                                                  "DELPHI_CODE_BITBUCKET_API_USERNAME": "me", "DELPHI_CODE_BITBUCKET_API_PASSWORD": "pw"}))

    def serve(self, pages):
        requests = []

        def urlopen(request, timeout, context):
            requests.append(request)
            page = pages[request.full_url]
            if isinstance(page, int):
                raise HTTPError(request.full_url, page, "Not Found", {}, io.BytesIO())
            return io.BytesIO(json.dumps(page).encode())

        self.enterContext(patch.object(bitbucket_api, "urlopen", side_effect=urlopen))
        self.enterContext(patch.object(bitbucket_api, "verified_tls"))
        return requests

    def test_pages_are_followed_and_credentials_sent(self):
        first, second = bitbucket_api.repositories_url("acme"), "https://api.test/2.0/next-page"
        requests = self.serve({
            first: {"values": [{"slug": "api", "name": "API", "is_private": True}], "next": second},
            second: {"values": [{"slug": "web", "description": "Site"}]},
        })
        self.assertEqual([(item["slug"], item["private"], item["description"]) for item in bitbucket_api.repositories("acme")],
                         [("api", True, ""), ("web", False, "Site")])
        self.assertEqual(requests[0].get_header("Authorization"), "Basic bWU6cHc=")

    def test_workspaces_fall_back_to_the_permissions_endpoint(self):
        self.serve({
            bitbucket_api.workspaces_url(): 410,
            bitbucket_api.legacy_workspaces_url(): {"values": [{"workspace": {"slug": "zeta", "name": "Zeta"}}, {"workspace": {"slug": "acme"}}]},
        })
        self.assertEqual(bitbucket_api.workspaces(), [{"slug": "acme", "name": "acme"}, {"slug": "zeta", "name": "Zeta"}])

    def test_http_errors_are_reported_as_json(self):
        self.serve({bitbucket_api.workspaces_url(): 401})
        output = io.StringIO()
        with patch.object(sys, "argv", ["bitbucket_api.py", "workspaces"]), patch("sys.stdout", output), self.assertRaises(SystemExit):
            bitbucket_api.main()
        self.assertEqual(json.loads(output.getvalue())["error"]["status"], 401)


class Catalog(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_catalog_runs_the_api_script_in_a_child_process(self):
        api = self.root / "api"
        with patch.dict(os.environ, {"DELPHI_CODE_BITBUCKET_API_BASE": api.as_uri()}):
            for url, page in ((bitbucket_api.workspaces_url(), {"values": [{"workspace": {"slug": "acme", "name": "Acme"}}]}),
                              (bitbucket_api.repositories_url("acme"), {"values": [{"slug": "api", "description": "Public API"}]})):
                path = Path(url2pathname(url.removeprefix("file://")))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(page))
            catalog = BitbucketCatalog(("me", "pw"))
            self.assertEqual(catalog.workspaces(), [Workspace("acme", "Acme")])
            self.assertEqual(catalog.repositories("acme"), [Repository("acme", "api", "Public API", False)])
            with self.assertRaises(Failure) as raised:
                catalog.repositories("missing")
            self.assertEqual(raised.exception.code, "remote_unavailable")

    def test_rejected_credentials(self):
        rejected = subprocess.CompletedProcess([], 1, json.dumps({"error": {"status": 401, "message": "HTTP 401 Unauthorized"}}), "")
        with patch("delphi_code.bitbucket_catalog.subprocess.run", return_value=rejected), self.assertRaises(Failure) as raised:
            BitbucketCatalog(("me", "pw")).workspaces()
        self.assertEqual(raised.exception.code, "remote_auth_failed")

    def api_username_and_password(self):
        catalog = BitbucketCatalog()
        answered = subprocess.CompletedProcess([], 0, "[]", "")
        with patch("delphi_code.bitbucket_catalog.subprocess.run", return_value=answered) as run:
            catalog.workspaces()
        environment = run.call_args.kwargs["env"]
        return environment["DELPHI_CODE_BITBUCKET_API_USERNAME"], environment["DELPHI_CODE_BITBUCKET_API_PASSWORD"]

    def test_credentials_prefer_environment_then_git(self):
        store = self.root / "git-credentials"
        config = self.root / "gitconfig"
        config.write_text(f"[credential]\n\thelper = store --file {store}\n")
        isolated = {"GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(self.root)}
        with patch.dict(os.environ, isolated):
            for name in ("BITBUCKET_USERNAME", "BITBUCKET_APP_PASSWORD", "BITBUCKET_EMAIL"):
                os.environ.pop(name, None)
            with self.assertRaises(Failure) as raised:
                BitbucketCatalog()
            self.assertEqual(raised.exception.code, "remote_auth_missing")
            store.write_text("https://stored-user:stored-pass@bitbucket.org\n")
            self.assertEqual(self.api_username_and_password(), ("stored-user", "stored-pass"))
            with patch.dict(os.environ, {"BITBUCKET_USERNAME": "me", "BITBUCKET_APP_PASSWORD": "pw"}):
                self.assertEqual(self.api_username_and_password(), ("me", "pw"))
                with patch.dict(os.environ, {"BITBUCKET_EMAIL": "me@example.com"}):
                    self.assertEqual(self.api_username_and_password(), ("me@example.com", "pw"))
