import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import sqlite_vec

from delphi_code.cli import arguments, execute
from delphi_code.errors import Failure
from delphi_code.keys import local_key
from delphi_code.store import Store
from fakes import use_fake_model


class CrossProjectSearch(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.enterContext(patch.dict(os.environ, {"DELPHI_CODE_INDEX_ROOT": str(self.root / "indexes")}))
        self.model = use_fake_model(self, self.root / "model")

    def register(self, name, rows, **updates):
        project = self.root / name
        project.mkdir()
        state = Store().get_or_create(local_key(project), project).directory
        (state / "lock").touch()
        info = dict(schema_version=2, project=str(project), source={"key": local_key(project)}, ready=True, model_sha256="same-model")
        info.update(updates)
        (state / "manifest.json").write_text(json.dumps(info))
        db = sqlite3.connect(state / "vectors.sqlite")
        try:
            db.enable_load_extension(True)
            sqlite_vec.load(db)
            db.execute("CREATE VIRTUAL TABLE passages USING vec0(id INTEGER PRIMARY KEY, vector FLOAT[2], +path TEXT, +language TEXT, +text TEXT, +start_line INTEGER, +end_line INTEGER)")
            for identifier, (path, language, vector) in enumerate(rows):
                db.execute("INSERT INTO passages(id,vector,path,language,text,start_line,end_line) VALUES (?,?,?,?,?,1,1)",
                           (identifier, np.array(vector, dtype=np.float32).tobytes(), path, language, path))
            db.commit()
        finally:
            db.close()
        return project, state

    def search(self, *options):
        with patch.object(sys, "argv", ["delphi-code", "search", "query", *options]):
            return execute(arguments())

    def test_global_ranking_limit_and_project_identity(self):
        first, _ = self.register("one", [("a.py", "python", [0, 1]), ("b.py", "python", [0.8, 0.6])])
        second, _ = self.register("two", [("best.py", "python", [1, 0])])
        data = self.search("--limit", "2")
        self.assertIsNone(data["project"])
        self.assertEqual(data["projects"], sorted([str(first), str(second)]))
        self.assertEqual([(r["project"], r["path"]) for r in data["results"]], [(str(second), "best.py"), (str(first), "b.py")])
        self.assertEqual(len(self.model.embedded), 1)
        scoped = self.search("-p", "one", "--limit", "1")
        self.assertEqual(scoped["project"], str(first))
        self.assertEqual(scoped["results"][0]["path"], "b.py")

    def test_filters_and_empty_matches(self):
        self.register("one", [("src/a.py", "python", [0, 1])])
        self.register("two", [("src/a.js", "javascript", [1, 0]), ("test.py", "python", [1, 0])])
        data = self.search("--path", "src/*", "--language", "python")
        self.assertEqual([r["path"] for r in data["results"]], ["src/a.py"])
        self.assertEqual(self.search("--language", "rust")["results"], [])

    def test_missing_indexes_and_invalid_limit(self):
        with self.assertRaises(Failure) as error:
            self.search()
        self.assertEqual(error.exception.code, "index_missing")
        with self.assertRaises(Failure) as error:
            self.search("--limit", "0")
        self.assertEqual(error.exception.code, "usage")

    def test_incompatible_or_incomplete_index_fails_without_partial_results(self):
        self.register("one", [("a.py", "python", [1, 0])])
        _, state = self.register("two", [], model_sha256="different")
        with self.assertRaises(Failure) as error:
            self.search()
        self.assertEqual(error.exception.code, "model_mismatch")
        info = json.loads((state / "manifest.json").read_text())
        info.update(model_sha256="same-model", ready=False)
        (state / "manifest.json").write_text(json.dumps(info))
        with self.assertRaises(Failure) as error:
            self.search()
        self.assertEqual(error.exception.code, "index_incomplete")
