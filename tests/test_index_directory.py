import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from delphi_code.keys import canonical, project_key
from delphi_code.paths import data_directory, index_root
from delphi_code.store import Store


def git(project, *args):
    subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)


class Canonical(unittest.TestCase):
    def test_remote_forms_share_a_key(self):
        for remote in (
            "git@bitbucket.org:Acme/API.git", "https://user@bitbucket.org/acme/api.git", "ssh://git@bitbucket.org:22/acme/api",
            "https://bitbucket.org/acme/api/", "bitbucket.org/acme/api",
        ):
            self.assertEqual(canonical(remote), "bitbucket.org/acme/api", remote)

    def test_local_remotes_have_no_key(self):
        for remote in ("/srv/git/api.git", "../api", "file:///srv/git/api.git", "api", ""):
            self.assertIsNone(canonical(remote), remote)


class Identity(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.store = Store(self.root / "indexes")

    def test_default_is_user_data(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(index_root(), data_directory() / "indexes")

    def test_git_root_uses_remote_and_other_directories_use_path(self):
        project = self.root / "clone"
        project.mkdir()
        self.assertEqual(project_key(project), f"local:{project}")
        git(project, "init", "-q")
        self.assertEqual(project_key(project), f"local:{project}")
        git(project, "remote", "add", "origin", "git@bitbucket.org:acme/api.git")
        self.assertEqual(project_key(project), "bitbucket.org/acme/api")
        (project / "sub").mkdir()
        self.assertEqual(project_key(project / "sub"), f"local:{project / 'sub'}")

    def test_create_is_stable_and_separates_keys(self):
        first = self.store.create("bitbucket.org/acme/api", self.root / "one")
        self.assertEqual(self.store.create("bitbucket.org/acme/api", self.root / "two").directory, first.directory)
        second = self.store.create("local:/elsewhere/api", Path("/elsewhere/api"))
        self.assertNotEqual(first.directory, second.directory)
        self.assertEqual(self.store.get("bitbucket.org/acme/api").directory, first.directory)
        self.assertIsNone(self.store.get("bitbucket.org/acme/other"))
        with first.lock(True):
            self.assertTrue((first.directory / "lock").is_file())
        self.store.delete(first)
        self.assertFalse(first.directory.exists())
        self.assertEqual([index.key for index in self.store.all()], ["local:/elsewhere/api"])

    def test_legacy_indexes_are_migrated_in_place(self):
        project = self.root / "clone"
        project.mkdir()
        git(project, "init", "-q")
        git(project, "remote", "add", "origin", "https://github.com/acme/api.git")
        duplicate = self.root / "copy"
        duplicate.mkdir()
        git(duplicate, "init", "-q")
        git(duplicate, "remote", "add", "origin", "git@github.com:acme/api.git")
        states = {}
        for name, path in (("a", project), ("b", duplicate), ("c", self.root / "gone")):
            state = self.root / "indexes" / name
            state.mkdir(parents=True)
            (state / "manifest.json").write_text(json.dumps({"schema_version": 1, "project": str(path), "ready": True}))
            states[name] = state
        keys = {index.directory.name: index.key for index in self.store.all()}
        self.assertEqual(keys, {"a": "github.com/acme/api", "b": f"local:{duplicate}", "c": f"local:{self.root / 'gone'}"})
        manifest = json.loads((states["a"] / "manifest.json").read_text())
        self.assertEqual((manifest["schema_version"], manifest["ready"]), (2, True))
        self.assertEqual({index.directory.name: index.key for index in self.store.all()}, keys)
