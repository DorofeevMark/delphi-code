from pathlib import Path
import tempfile
import unittest

from delphi_code.domain.errors import Failure
from delphi_code.domain.keys import local_key
from delphi_code.infrastructure.store import Store


class ProjectNames(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.store = Store(self.root / "indexes")

    def register(self, relative, key=None):
        project = self.root / relative
        project.mkdir(parents=True)
        return project, self.store.get_or_create(key or local_key(project), project)

    def resolve(self, value):
        project, key, index = self.store.resolve(value)
        return project, key, index and index.directory

    def test_unique_name(self):
        project, index = self.register("one/flixbeton")
        self.assertEqual(self.resolve("flixbeton"), (project, local_key(project), index.directory))

    def test_remote_keys_and_suffixes(self):
        project, index = self.register("checkout", "bitbucket.org/acme/api")
        for name in ("bitbucket.org/acme/api", "acme/api", "api", "git@bitbucket.org:acme/api.git", "checkout"):
            self.assertEqual(self.resolve(name), (project, "bitbucket.org/acme/api", index.directory), name)

    def test_duplicate_names_require_path(self):
        first, _ = self.register("one/flixbeton")
        second, _ = self.register("two/flixbeton")
        with self.assertRaises(Failure) as raised:
            self.resolve("flixbeton")
        self.assertEqual(raised.exception.code, "project_ambiguous")
        self.assertIn(str(first), str(raised.exception))
        self.assertIn(str(second), str(raised.exception))
        self.assertEqual(self.resolve(str(first))[0], first)
        self.assertEqual(self.resolve("./flixbeton")[0], Path("flixbeton").resolve())

    def test_unindexed_paths_still_work(self):
        self.assertEqual(
            self.resolve("unindexed")[:2], (Path("unindexed").resolve(), local_key(Path("unindexed").resolve()))
        )
        self.assertIsNone(self.resolve("unindexed")[2])
        self.assertEqual(self.resolve(".")[0], Path.cwd())

    def test_invalid_manifests_do_not_break_lookup(self):
        project, _ = self.register("one/flixbeton")
        invalid = self.root / "indexes/invalid"
        invalid.mkdir()
        for content in (
            "{",
            "null",
            '{"project": 42}',
            '{"source": {"key": 7}}',
            '{"schema_version": 1, "project": "relative"}',
        ):
            (invalid / "manifest.json").write_text(content)
            self.assertEqual(self.resolve("flixbeton")[0], project)
