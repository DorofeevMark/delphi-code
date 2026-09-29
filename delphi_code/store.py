from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
import fcntl
import fnmatch
from pathlib import Path
import secrets
import shutil
import sqlite3
from typing import NamedTuple

from .errors import ExitCode, Failure
from .keys import is_explicit_path, local_key, match_key, project_key
from .manifest import LEGACY_SCHEMA_VERSION, SCHEMA_VERSION, Manifest
from .model import LocalModel
from .paths import index_root


def require_sqlite_extensions():
    if not hasattr(sqlite3.Connection, "enable_load_extension"):
        raise Failure(
            "sqlite_extensions_unavailable",
            "This Python disables SQLite extension loading; provision a Python build with loadable SQLite extensions",
            ExitCode.RUNTIME_ASSETS,
        )


@contextmanager
def vector_database(location: str, uri: bool = False) -> Iterator[sqlite3.Connection]:
    import sqlite_vec

    db = sqlite3.connect(location, uri=uri)
    try:
        db.enable_load_extension(True)
        sqlite_vec.load(db)
        db.enable_load_extension(False)
        db.row_factory = sqlite3.Row
        yield db
    finally:
        db.close()


@dataclass(frozen=True)
class Index:
    directory: Path
    key: str
    project: Path | None
    manifest: Manifest = field(compare=False, repr=False)

    @classmethod
    def load_if_keyed(cls, directory: Path) -> Index | None:
        manifest = Manifest.read_if_valid_json(directory / "manifest.json")
        if manifest is None or manifest.key is None:
            return None
        return cls(directory, manifest.key, manifest.project, manifest)

    @contextmanager
    def lock(self, exclusive: bool) -> Iterator[None]:
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

    def read_manifest(self) -> Manifest:
        manifest = Manifest.read_if_valid_json(self._manifest_path)
        if manifest is None:
            raise Failure("index_missing", "No completed index exists; run index first", ExitCode.INDEX_STATE)
        if manifest.schema_version != SCHEMA_VERSION:
            raise Failure("index_incompatible", "Unsupported index version", ExitCode.INDEX_STATE)
        return manifest

    def write_manifest(self, manifest: Manifest):
        manifest.write(self._manifest_path)

    def read_manifest_compatible_with(self, model: LocalModel) -> Manifest:
        manifest = self.read_manifest()
        if manifest.conflicts_with(model):
            raise Failure(
                "model_mismatch",
                f"Model assets differ from the index; restore the original model or move {self.directory} aside and reindex",
                ExitCode.INDEX_STATE,
            )
        return manifest

    def read_searchable_manifest(self, model: LocalModel) -> Manifest:
        manifest = self.read_manifest_compatible_with(model)
        if not manifest.ready:
            raise Failure(
                "index_incomplete",
                "Last index did not complete; run index again before searching",
                ExitCode.INDEX_STATE,
            )
        return manifest

    def counts(self) -> dict:
        with self._database() as db:
            return dict(db.execute("SELECT count(*) AS chunks, count(DISTINCT path) AS files FROM passages").fetchone())

    def search(self, query_vector: bytes, paths: list[str], languages: list[str], limit: int) -> list[dict]:
        conditions, parameters = [], [query_vector]
        if languages:
            conditions.append("language IN (" + ",".join("?" for _ in languages) + ")")
            parameters.extend(languages)
        if paths:
            conditions.append("(" + " OR ".join("path_matches(path, ?)" for _ in paths) + ")")
            parameters.extend(paths)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self._database() as db:
            db.create_function("path_matches", 2, fnmatch.fnmatchcase, deterministic=True)
            rows = db.execute(
                "SELECT path, language, text, start_line, end_line, vec_distance_L2(vector, ?) AS distance "
                "FROM passages" + where + " ORDER BY distance, path, start_line, id LIMIT ?",
                [*parameters, limit],
            ).fetchall()
        return [{**dict(row), "score": _cosine_similarity_of_unit_vectors(row["distance"])} for row in rows]

    @property
    def _manifest_path(self) -> Path:
        return self.directory / "manifest.json"

    def _database(self):
        return vector_database((self.directory / "vectors.sqlite").as_uri() + "?mode=ro", uri=True)


class ResolvedProject(NamedTuple):
    project: Path | None
    key: str
    index: Index | None


class Store:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else index_root()

    def all(self) -> list[Index]:
        indexes, legacy_directories = self._read_directories()
        if not legacy_directories:
            return indexes
        with self._exclusively():
            return self._assign_keys_to_legacy_indexes(*self._read_directories())

    def unrecognized_directories(self) -> list[Path]:
        recognized = {index.directory for index in self.all()}
        return sorted(path for path in self.root.glob("*") if path.is_dir() and path not in recognized)

    def get(self, key: str) -> Index | None:
        return next((index for index in self.all() if index.key == key), None)

    def get_or_create(self, key: str, project: Path | None) -> Index:
        with self._exclusively():
            indexes = self._assign_keys_to_legacy_indexes(*self._read_directories())
            existing = next((index for index in indexes if index.key == key), None)
            if existing:
                return existing
            manifest = Manifest.placeholder(key, project)
            index = Index(self.root / secrets.token_hex(32), key, project, manifest)
            index.directory.mkdir(parents=True)
            index.write_manifest(manifest)
            return index

    def delete(self, index: Index):
        with index.lock(True):
            shutil.rmtree(index.directory)

    def resolve(self, name: str | Path) -> ResolvedProject:
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
    def _exclusively(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / ".store.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _read_directories(self) -> tuple[list[Index], list[Path]]:
        indexes, legacy_directories = [], []
        for manifest_path in sorted(self.root.glob("*/manifest.json")):
            if index := Index.load_if_keyed(manifest_path.parent):
                indexes.append(index)
            elif _is_legacy(Manifest.read_if_valid_json(manifest_path)):
                legacy_directories.append(manifest_path.parent)
        return indexes, legacy_directories

    def _assign_keys_to_legacy_indexes(self, indexes: list[Index], legacy_directories: list[Path]) -> list[Index]:
        taken_keys = {index.key for index in indexes}
        for directory in legacy_directories:
            with (directory / "lock").open("a") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                legacy = Manifest.read_if_valid_json(directory / "manifest.json")
                project = legacy.project
                key = project_key(project)
                unique_key = key if key not in taken_keys else local_key(project)
                manifest = legacy.upgraded_with_key(unique_key)
                index = Index(directory, unique_key, project, manifest)
                index.write_manifest(manifest)
            taken_keys.add(unique_key)
            indexes.append(index)
        return sorted(indexes, key=lambda index: index.directory)


def _is_legacy(manifest: Manifest | None) -> bool:
    return manifest is not None and manifest.schema_version == LEGACY_SCHEMA_VERSION and manifest.project is not None


def _cosine_similarity_of_unit_vectors(l2_distance: float) -> float:
    return max(-1.0, min(1.0, 1.0 - l2_distance**2 / 2.0))
