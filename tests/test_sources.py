import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from delphi_code.cli import arguments, execute
from delphi_code.errors import Failure
from delphi_code.picker import TrackingChanges
from delphi_code.registry import Registry
from delphi_code.hosts import Bitbucket, GitHub
from delphi_code.sources import GitRemoteSource, LocalSource, source_from_registry, source_from_argument
from delphi_code.manifest import Manifest
from delphi_code.store import Store
from fakes import use_fake_model


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class LocalHostWithApiRepository(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.base = self.root / "remote"
        self.enterContext(patch.dict(os.environ, {
            "DELPHI_CODE_BITBUCKET_GIT_BASE": self.base.as_uri(), "DELPHI_CODE_GITHUB_GIT_BASE": self.base.as_uri(),
            "DELPHI_CODE_INDEX_ROOT": str(self.root / "indexes"),
            "DELPHI_CODE_REGISTRY": str(self.root / "repos.toml"),
        }))
        for name in ("BITBUCKET_USERNAME", "BITBUCKET_APP_PASSWORD", "GITHUB_TOKEN", "GH_TOKEN"):
            os.environ.pop(name, None)
        git("init", "-q", "--bare", "-b", "main", str(self.base / "acme/api.git"))
        self.work = self.root / "work"
        git("clone", "-q", str(self.base / "acme/api.git"), str(self.work))
        git("symbolic-ref", "HEAD", "refs/heads/main", cwd=self.work)
        git("config", "user.email", "test@example.com", cwd=self.work)
        git("config", "user.name", "Test", cwd=self.work)
        self.commit("parse.py", "def parse(text):\n    return text\n")
        git("tag", "v1", cwd=self.work)
        git("push", "-q", "origin", "main", "v1", cwd=self.work)

    def commit(self, name, text):
        (self.work / name).write_text(text)
        git("add", name, cwd=self.work)
        git("commit", "-q", "-m", name, cwd=self.work)
        return git("rev-parse", "HEAD", cwd=self.work)


class ParseSource(unittest.TestCase):
    def test_forms(self):
        for text in ("bitbucket.org/acme/api", "git@bitbucket.org:acme/api.git", "https://bitbucket.org/Acme/API"):
            source = source_from_registry(text)
            self.assertIsInstance(source.host, Bitbucket, text)
            self.assertEqual(source.key, "bitbucket.org/acme/api")
        for text in ("github.com/acme/api", "git@github.com:Acme/API.git", "https://github.com/acme/api"):
            source = source_from_registry(text)
            self.assertIsInstance(source.host, GitHub, text)
            self.assertEqual(source.key, "github.com/acme/api")
        self.assertIsInstance(source_from_registry("/src/api"), LocalSource)
        for text in ("gitlab.com/acme/api", "github.com/acme", "bitbucket.org/acme", "bitbucket.org/acme/api/extra", "relative/path"):
            self.assertIsNone(source_from_registry(text), text)

    def test_arguments_prefer_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            os.makedirs(Path(temporary) / "bitbucket.org/acme/api")
            previous = Path.cwd()
            os.chdir(temporary)
            self.addCleanup(os.chdir, previous)
            self.assertIsInstance(source_from_argument("bitbucket.org/acme/api"), LocalSource)
            os.chdir(previous)
        self.assertIsInstance(source_from_argument("bitbucket.org/acme/api"), GitRemoteSource)
        with self.assertRaises(Failure) as raised:
            source_from_argument("gitlab.com/acme/api")
        self.assertEqual(raised.exception.code, "usage")


class RemoteGitCheckouts(LocalHostWithApiRepository):
    def test_latest_and_checkout(self):
        source = GitRemoteSource(Bitbucket(), "acme", "api")
        head = git("rev-parse", "HEAD", cwd=self.work)
        self.assertEqual(source.latest_revision(None), ("main", head))
        self.assertEqual(source.latest_revision("v1"), ("v1", head))
        with self.assertRaises(Failure) as raised:
            source.latest_revision("missing")
        self.assertEqual(raised.exception.code, "remote_ref_missing")
        with source.checkout() as checkout:
            directory = checkout.directory
            self.assertEqual((directory / "parse.py").read_text(), "def parse(text):\n    return text\n")
            self.assertIsNone(checkout.project)
            self.assertEqual(checkout.provenance["commit"], head)
            self.assertEqual(checkout.provenance["permalink"], f"https://bitbucket.org/acme/api/src/{head}/{{path}}#lines-{{start}}:{{end}}")
        self.assertFalse(directory.exists())

    def test_missing_repository(self):
        with self.assertRaises(Failure) as raised:
            GitRemoteSource(Bitbucket(), "acme", "missing").latest_revision(None)
        self.assertEqual(raised.exception.code, "remote_unavailable")

    def git_call_with_askpass_answers(self, source):
        calls = []
        real = subprocess.run

        def run(command, **options):
            askpass = options["env"].get("GIT_ASKPASS")
            answers = [real([askpass, prompt], capture_output=True, text=True, env=options["env"]).stdout for prompt in ("Username for 'https://host': ", "Password for 'https://user@host': ")] if askpass else None
            calls.append((command, answers))
            return real(command, **options)

        with patch("delphi_code.sources.subprocess.run", side_effect=run):
            source.latest_revision(None)
        return calls[0]

    def test_credentials_reach_git_only_through_askpass(self):
        with patch.dict(os.environ, {"BITBUCKET_USERNAME": "me", "BITBUCKET_APP_PASSWORD": "s3cret"}):
            command, answers = self.git_call_with_askpass_answers(GitRemoteSource(Bitbucket(), "acme", "api"))
        self.assertEqual(command[1:3], ["-c", "credential.helper="])
        self.assertNotIn("s3cret", " ".join(command))
        self.assertEqual(answers, ["me\n", "s3cret\n"])
        with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_token"}):
            self.assertEqual(self.git_call_with_askpass_answers(GitRemoteSource(GitHub(), "acme", "api"))[1], ["x-access-token\n", "ghp_token\n"])
        command, answers = self.git_call_with_askpass_answers(GitRemoteSource(Bitbucket(), "acme", "api"))
        self.assertEqual((command[1], answers), ("ls-remote", None))

    def test_github_checkout_links_to_github(self):
        head = git("rev-parse", "HEAD", cwd=self.work)
        with GitRemoteSource(GitHub(), "acme", "api").checkout() as checkout:
            self.assertEqual((checkout.provenance["kind"], checkout.provenance["key"]), ("github", "github.com/acme/api"))
            self.assertEqual(checkout.provenance["permalink"], f"https://github.com/acme/api/blob/{head}/{{path}}#L{{start}}-L{{end}}")


class RemoteSync(LocalHostWithApiRepository):
    def setUp(self):
        super().setUp()
        use_fake_model(self, self.root / "model")
        self.checkouts = []

        def index_checkout(store, checkout, options, model):
            self.checkouts.append(checkout)
            index = store.get_or_create(checkout.key, checkout.project)
            index.write_manifest(Manifest.for_build(checkout, options, model).completed())
            return {"project": None, "key": index.key, "index_directory": str(index.directory), "commit": checkout.provenance.get("commit")}

        self.enterContext(patch("delphi_code.projects.index_checkout", side_effect=index_checkout))

    def run_command(self, *args):
        with patch.object(sys, "argv", ["delphi-code", *args]):
            return execute(arguments())

    def test_add_sync_skips_unchanged_and_follows_new_commits(self):
        added = self.run_command("add", "git@bitbucket.org:acme/api.git")["repos"][0]
        self.assertEqual((added["key"], added["unchanged"]), ("bitbucket.org/acme/api", False))
        self.assertEqual(Registry().entries()[0].source, "bitbucket.org/acme/api")
        self.assertFalse(self.checkouts[0].directory.exists())
        self.assertTrue(self.run_command("sync")["repos"][0]["unchanged"])
        self.assertEqual(len(self.checkouts), 1)
        newer = self.commit("more.py", "VALUE = 1\n")
        git("push", "-q", "origin", "main", cwd=self.work)
        synced = self.run_command("sync")["repos"][0]
        self.assertEqual((synced["unchanged"], synced["commit"]), (False, newer))
        self.run_command("add", "bitbucket.org/acme/api", "--path", "src/*")
        self.assertEqual(len(self.checkouts), 3)
        self.assertEqual(len(Registry().entries()), 1)

    def test_ref_is_recorded_and_rejected_for_local_projects(self):
        self.run_command("add", "bitbucket.org/acme/api", "--ref", "v1")
        self.assertEqual(Registry().entries()[0].ref, "v1")
        self.assertEqual(self.checkouts[0].provenance["ref"], "v1")
        with self.assertRaises(Failure) as raised:
            self.run_command("add", str(self.work), "--ref", "main")
        self.assertEqual(raised.exception.code, "usage")

    def test_remote_index_rejects_plain_index_and_lists_commit(self):
        self.run_command("add", "bitbucket.org/acme/api")
        with self.assertRaises(Failure) as raised:
            self.run_command("index", "-p", "acme/api")
        self.assertEqual(raised.exception.code, "usage")
        row = self.run_command("list")["repos"][0]
        self.assertEqual((row["key"], row["tracked"], row["project"], row["ref"]), ("bitbucket.org/acme/api", True, None, "main"))
        self.run_command("remove", "acme/api")
        self.assertEqual((Registry().entries(), Store().all()), ([], []))

    def pick(self, changes):
        picker = self.enterContext(patch("delphi_code.picker.RepositoryPicker"))
        picker.return_value.choose.return_value = changes
        return picker.return_value.choose

    def test_add_without_sources_needs_a_terminal(self):
        with patch.object(sys.stdin, "isatty", return_value=False), self.assertRaises(Failure) as raised:
            self.run_command("add")
        self.assertEqual(raised.exception.code, "usage")

    def test_picked_repositories_are_tracked_indexed_and_untracked(self):
        self.run_command("add", str(self.work))
        picker = self.pick(TrackingChanges(["bitbucket.org/acme/api"], []))
        added = self.run_command("add", "--path", "src/*")
        self.assertEqual(picker.call_args.args[0], {f"local:{self.work}"})
        self.assertEqual(([repo["key"] for repo in added["repos"]], added["removed"], added["cancelled"]), (["bitbucket.org/acme/api"], [], False))
        self.assertEqual([(entry.source, entry.selection.paths) for entry in Registry().entries()],
                         [(str(self.work), []), ("bitbucket.org/acme/api", ["src/*"])])
        index_directory = Store().get("bitbucket.org/acme/api").directory
        picker.return_value = TrackingChanges([], ["bitbucket.org/acme/api"])
        removed = self.run_command("add")
        self.assertEqual(removed["removed"], [{"key": "bitbucket.org/acme/api", "deleted_index": str(index_directory)}])
        self.assertEqual([entry.source for entry in Registry().entries()], [str(self.work)])
        self.assertFalse(index_directory.exists())

    def test_cancelled_pick_changes_nothing(self):
        self.pick(None)
        self.assertEqual(self.run_command("add")["cancelled"], True)
        self.assertEqual(Registry().entries(), [])
