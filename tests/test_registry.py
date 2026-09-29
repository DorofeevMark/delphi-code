import os
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from fakes import use_fake_model

from delphi_code.cli import arguments, execute
from delphi_code.errors import Failure
from delphi_code.keys import local_key
from delphi_code.registry import Entry, Registry
from delphi_code.selection import FileSelection
from delphi_code.store import Store


class RegistryCommands(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.registry = self.root / "repos.toml"
        self.enterContext(
            patch.dict(
                os.environ,
                {"DELPHI_CODE_INDEX_ROOT": str(self.root / "indexes"), "DELPHI_CODE_REGISTRY": str(self.registry)},
            )
        )
        use_fake_model(self, self.root / "model")
        self.indexed = []

        def index_checkout(store, checkout, options, model):
            key = checkout.key
            self.indexed.append((checkout.project, key, options))
            index = store.get_or_create(key, checkout.project)
            return {"project": str(checkout.project), "key": key, "index_directory": str(index.directory)}

        self.enterContext(patch("delphi_code.projects.index_checkout", side_effect=index_checkout))

    def run_command(self, *args):
        with patch.object(sys, "argv", ["delphi-code", *args]):
            return execute(arguments())

    def save(self, *entries):
        with Registry().edit() as current:
            current[:] = entries

    def project(self, name):
        path = self.root / name
        path.mkdir()
        return path

    def test_add_writes_registry_and_indexes(self):
        one, two = self.project("one"), self.project("two")
        data = self.run_command("add", str(one), str(two), "--path", "src/*", "--max-bytes", "2048")
        self.assertEqual([repo["key"] for repo in data["repos"]], [local_key(one), local_key(two)])
        self.assertTrue(all(repo["ok"] for repo in data["repos"]))
        stored = tomllib.loads(self.registry.read_text())["repo"]
        self.assertEqual(stored[0], {"source": str(one), "key": local_key(one), "paths": ["src/*"], "max_bytes": 2048})
        self.assertEqual(self.indexed[0][2], FileSelection(["src/*"], [], [], 2048))

    def test_add_replaces_existing_entry(self):
        one = self.project("one")
        self.run_command("add", str(one), "--language", "python", "--no-sync")
        self.run_command("add", str(one), "--no-sync")
        self.assertEqual(Registry().entries(), [Entry(str(one), local_key(one))])
        self.assertEqual(self.indexed, [])

    def test_add_rejects_missing_directory(self):
        with self.assertRaises(Failure) as raised:
            self.run_command("add", str(self.root / "missing"))
        self.assertEqual(raised.exception.code, "project_missing")
        self.assertFalse(self.registry.exists())

    def test_sync_reports_each_failure_and_keeps_going(self):
        one = self.project("one")
        self.save(Entry(str(self.root / "gone")), Entry(str(one)), Entry(str(self.root / "gone-too")))
        with self.assertRaises(Failure) as raised:
            self.run_command("sync")
        self.assertEqual(raised.exception.code, "sync_failed")
        repos = raised.exception.data["repos"]
        self.assertEqual([repo["ok"] for repo in repos], [False, True, False])
        self.assertEqual(repos[0]["error"]["code"], "project_missing")
        self.assertEqual([item[0] for item in self.indexed], [one])

    def test_moved_checkout_keeps_recorded_key(self):
        one = self.project("one")
        self.run_command("add", str(one), "--no-sync")
        self.save(Entry(str(one), "bitbucket.org/acme/one"))
        one.rmdir()
        rows = self.run_command("list")["repos"]
        self.assertEqual([(row["key"], row["tracked"]) for row in rows], [("bitbucket.org/acme/one", True)])

    def test_sync_with_empty_registry(self):
        self.assertEqual(self.run_command("sync")["repos"], [])

    def test_list_combines_registry_and_untracked_indexes(self):
        one, two = self.project("one"), self.project("two")
        self.run_command("add", str(one), "--no-sync")
        Store().get_or_create(local_key(two), two)
        rows = {row["key"]: row for row in self.run_command("list")["repos"]}
        self.assertEqual(rows[local_key(one)]["tracked"], True)
        self.assertIsNone(rows[local_key(one)]["index_directory"])
        self.assertEqual(rows[local_key(two)]["tracked"], False)
        self.assertEqual(rows[local_key(two)]["project"], str(two))

    def test_remove_untracks_and_deletes_index(self):
        one, two = self.project("one"), self.project("two")
        self.run_command("add", str(one), str(two))
        state = Store().get(local_key(one)).directory
        data = self.run_command("remove", "one")
        self.assertEqual(data, {"key": local_key(one), "untracked": True, "deleted_index": str(state)})
        self.assertFalse(state.exists())
        self.assertEqual(Registry().entries(), [Entry(str(two), local_key(two))])
        kept = self.run_command("remove", str(two), "--keep-index")
        self.assertIsNone(kept["deleted_index"])
        self.assertIsNotNone(Store().get(local_key(two)))
        with self.assertRaises(Failure) as raised:
            self.run_command("remove", "nothing")
        self.assertEqual(raised.exception.code, "project_missing")

    def test_invalid_registry(self):
        for content in (
            "[[repo]]\nsource = 'relative/path'\n",
            "[[repo]]\nsource = 'gitlab.com/acme/api'\n",
            "[[repo]]\npath = 1\n",
            "repo = 3\n",
            "[[repo]]\nsource = 'x'\nmax_bytes = 0\n",
            "[[repo]]\nsource = 'x'\nkey = 1\n",
            "not toml [",
        ):
            self.registry.write_text(content)
            with self.assertRaises(Failure) as raised:
                Registry().entries()
            self.assertEqual(raised.exception.code, "registry_invalid")


class RegistryFile(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.registry = Registry(Path(temporary.name) / "repos.toml")

    def test_round_trip_leaves_out_defaults(self):
        entries = [
            Entry("/src/api", "bitbucket.org/acme/api", FileSelection(paths=["src/*"])),
            Entry("~/web", selection=FileSelection(max_bytes=10)),
        ]
        with self.registry.edit() as current:
            current.extend(entries)
        self.assertEqual(self.registry.entries(), entries)
        self.assertEqual(
            tomllib.loads(self.registry.path.read_text())["repo"],
            [
                {"source": "/src/api", "key": "bitbucket.org/acme/api", "paths": ["src/*"]},
                {"source": "~/web", "max_bytes": 10},
            ],
        )

    def test_unchanged_edit_keeps_file(self):
        self.registry.path.write_text("# hand-written comment\n[[repo]]\nsource = '/src/api'\n")
        with self.registry.edit() as current:
            current[0].selection.paths.append("src/*")
            current[0].selection.paths.pop()
        self.assertIn("hand-written comment", self.registry.path.read_text())
