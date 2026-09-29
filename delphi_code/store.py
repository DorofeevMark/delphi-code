from contextlib import contextmanager
from dataclasses import dataclass, field
import fcntl
import json
from pathlib import Path
import secrets
import shutil
import sqlite3
from typing import NamedTuple

from .keys import is_explicit_path, local_key, match_key, project_key
from .errors import ExitCode, Failure
from .paths import index_root

SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSION = 1


def read_json_object(path):
    try:
        info = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) else None


def recorded_project(info):
    project = info.get("project")
    return Path(project) if isinstance(project, str) and Path(project).is_absolute() else None


def recorded_key(info):
    source = info.get("source")
    key = source.get("key") if isinstance(source, dict) else None
    return key if isinstance(key, str) and key else None


@dataclass(frozen=True)
class Index:
    directory: Path
    key: str
    project: Path | None
    info: dict = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def load_if_keyed(cls, directory):
        info = read_json_object(directory / "manifest.json")
        key = recorded_key(info) if info else None
        return cls(directory, key, recorded_project(info), info) if key else None

    @contextmanager
    def lock(self, exclusive):
        if exclusive:
            self.directory.mkdir(parents=True, exist_ok=True)
        if not self.directory.is_dir():
            raise Failure("index_missing", "No index exists; run index first", ExitCode.INDEX_STATE)
        try:
            with (self.directory / "lock").open("a" if exclusive else "r") as lock:
                try:
                    fcntl.flock(lock, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise Failure("index_busy", "Another command is updating this project", ExitCode.OPERATION) from exc
                yield
        except FileNotFoundError as exc:
            raise Failure("index_missing", "No index exists; run index first", ExitCode.INDEX_STATE) from exc

    def read_manifest(self):
        if not (self.directory / "manifest.json").is_file():
            raise Failure("index_missing", "No completed index exists; run index first", ExitCode.INDEX_STATE)
        manifest = json.loads((self.directory / "manifest.json").read_text())
        if manifest.get("schema_version") != SCHEMA_VERSION:
            raise Failure("index_incompatible", "Unsupported index version", ExitCode.INDEX_STATE)
        return manifest

    def write_manifest(self, manifest):
        temporary = self.directory / "manifest.tmp"
        temporary.write_text(json.dumps(manifest, sort_keys=True) + "\n")
        temporary.replace(self.directory / "manifest.json")

    @contextmanager
    def database(self):
        import sqlite_vec

        db = sqlite3.connect((self.directory / "vectors.sqlite").as_uri() + "?mode=ro", uri=True)
        try:
            db.enable_load_extension(True)
            sqlite_vec.load(db)
            db.enable_load_extension(False)
            db.row_factory = sqlite3.Row
            yield db
        finally:
            db.close()

    def counts(self):
        with self.database() as db:
            return dict(db.execute("SELECT count(*) AS chunks, count(DISTINCT path) AS files FROM passages").fetchone())


class ResolvedProject(NamedTuple):
    project: Path | None
    key: str
    index: Index | None


class Store:
    def __init__(self, root=None):
        self.root = Path(root) if root else index_root()

    def all(self):
        indexes, legacy_directories = self._read_directories()
        if not legacy_directories:
            return indexes
        with self._exclusively():
            return self._assign_keys_to_legacy_indexes(*self._read_directories())

    def get(self, key):
        return next((index for index in self.all() if index.key == key), None)

    def get_or_create(self, key, project):
        with self._exclusively():
            indexes = self._assign_keys_to_legacy_indexes(*self._read_directories())
            existing = next((index for index in indexes if index.key == key), None)
            if existing:
                return existing
            index = Index(self.root / secrets.token_hex(32), key, project)
            index.directory.mkdir(parents=True)
            index.write_manifest({"schema_version": SCHEMA_VERSION, "project": str(project) if project else None,
                                  "source": {"key": key}, "ready": False})
            return index

    def delete(self, index):
        with index.lock(True):
            shutil.rmtree(index.directory)

    def resolve(self, name):
        name = str(name)
        if not is_explicit_path(name):
            indexes = self.all()
            key = match_key(name, [(index.key, index.project) for index in indexes])
            if key:
                index = next(index for index in indexes if index.key == key)
                return ResolvedProject(index.project, key, index)
        project = Path(name).expanduser().resolve()
        key = project_key(project)
        return ResolvedProject(project, key, self.get(key))

    @contextmanager
    def _exclusively(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / ".store.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _read_directories(self):
        indexes, legacy_directories = [], []
        for manifest in sorted(self.root.glob("*/manifest.json")):
            if index := Index.load_if_keyed(manifest.parent):
                indexes.append(index)
            elif (info := read_json_object(manifest)) and info.get("schema_version") == LEGACY_SCHEMA_VERSION and recorded_project(info):
                legacy_directories.append(manifest.parent)
        return indexes, legacy_directories

    def _assign_keys_to_legacy_indexes(self, indexes, legacy_directories):
        taken_keys = {index.key for index in indexes}
        for directory in legacy_directories:
            with (directory / "lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                info = read_json_object(directory / "manifest.json")
                project = recorded_project(info)
                key = project_key(project)
                unique_key = key if key not in taken_keys else local_key(project)
                info.update(schema_version=SCHEMA_VERSION, source={"key": unique_key})
                index = Index(directory, unique_key, project, info)
                index.write_manifest(info)
            taken_keys.add(unique_key)
            indexes.append(index)
        return sorted(indexes, key=lambda index: index.directory)
