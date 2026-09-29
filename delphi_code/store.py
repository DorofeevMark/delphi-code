"""Stored indexes: one directory per project key, with a manifest, a lock, and the vector table."""
from contextlib import contextmanager
from dataclasses import dataclass, field
import fcntl
import json
from pathlib import Path
import secrets
import shutil
import sqlite3
from typing import NamedTuple

from .keys import explicit_path, key_for, local_key, select
from .model import Failure
from .paths import index_root

SCHEMA_VERSION = 2


def read_json(path):
    try:
        info = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return info if isinstance(info, dict) else None


def recorded_project(info):
    project = info.get("project")
    return Path(project) if isinstance(project, str) and Path(project).is_absolute() else None


@dataclass(frozen=True)
class Index:
    directory: Path
    key: str
    project: Path | None
    info: dict = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def load(cls, directory):
        """The index in directory, or None when its manifest has no valid key."""
        info = read_json(directory / "manifest.json")
        source = info.get("source") if info else None
        key = source.get("key") if isinstance(source, dict) else None
        if not isinstance(key, str) or not key:
            return None
        return cls(directory, key, recorded_project(info), info)

    @contextmanager
    def lock(self, exclusive):
        if exclusive:
            self.directory.mkdir(parents=True, exist_ok=True)
        if not self.directory.is_dir():
            raise Failure("index_missing", "No index exists; run index first", 4)
        try:
            with (self.directory / "lock").open("a" if exclusive else "r") as lock:
                try:
                    fcntl.flock(lock, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise Failure("index_busy", "Another command is updating this project", 5) from exc
                yield
        except FileNotFoundError as exc:
            raise Failure("index_missing", "No index exists; run index first", 4) from exc

    def manifest(self):
        """The current manifest, checked for a supported schema; call with the lock held."""
        if not (self.directory / "manifest.json").is_file():
            raise Failure("index_missing", "No completed index exists; run index first", 4)
        result = json.loads((self.directory / "manifest.json").read_text())
        if result.get("schema_version") != SCHEMA_VERSION:
            raise Failure("index_incompatible", "Unsupported index version", 4)
        return result

    def write(self, info):
        temporary = self.directory / "manifest.tmp"
        temporary.write_text(json.dumps(info, sort_keys=True) + "\n")
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


class Target(NamedTuple):
    """What a --project value names: a project directory, its key, and its index if one exists."""
    project: Path | None
    key: str
    index: Index | None


class Store:
    """All indexes under one root. Indexes from schema version 1 are migrated on first sight."""

    def __init__(self, root=None):
        self.root = Path(root) if root else index_root()

    def all(self):
        indexes, legacy = self._scan()
        if not legacy:
            return indexes
        with self._exclusive():
            return self._migrate(*self._scan())

    def get(self, key):
        return next((index for index in self.all() if index.key == key), None)

    def create(self, key, project):
        """The index for key, created with a placeholder manifest if none exists."""
        with self._exclusive():
            existing = next((index for index in self._migrate(*self._scan()) if index.key == key), None)
            if existing:
                return existing
            index = Index(self.root / secrets.token_hex(32), key, project)
            index.directory.mkdir(parents=True)
            index.write({"schema_version": SCHEMA_VERSION, "project": str(project), "source": {"key": key}, "ready": False})
            return index

    def delete(self, index):
        with index.lock(True):
            shutil.rmtree(index.directory)

    def resolve(self, value):
        raw = str(value)
        if not explicit_path(raw):
            indexes = self.all()
            key = select(raw, [(index.key, index.project) for index in indexes])
            if key:
                index = next(index for index in indexes if index.key == key)
                return Target(index.project, key, index)
        project = Path(raw).expanduser().resolve()
        key = key_for(project)
        return Target(project, key, self.get(key))

    @contextmanager
    def _exclusive(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / ".store.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _scan(self):
        indexes, legacy = [], []
        for manifest in sorted(self.root.glob("*/manifest.json")):
            if index := Index.load(manifest.parent):
                indexes.append(index)
            elif (info := read_json(manifest)) and info.get("schema_version") == 1 and recorded_project(info):
                legacy.append(manifest.parent)
        return indexes, legacy

    def _migrate(self, indexes, legacy):
        """Gives legacy path-hash indexes a key in place; call with the store lock held.

        A busy legacy index is skipped and migrated later. A remote key already taken by another
        index falls back to the path key, so two indexes never share a key.
        """
        taken = {index.key for index in indexes}
        for directory in legacy:
            with (directory / "lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                info = read_json(directory / "manifest.json")
                project = recorded_project(info)
                key = key_for(project)
                info.update(schema_version=SCHEMA_VERSION, source={"key": local_key(project) if key in taken else key})
                index = Index(directory, info["source"]["key"], project, info)
                index.write(info)
            taken.add(index.key)
            indexes.append(index)
        return sorted(indexes, key=lambda index: index.directory)
