from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import copy
from dataclasses import dataclass, field
import fcntl
from functools import cached_property
import json
from pathlib import Path
import tomllib

from ..domain.errors import ExitCode, Failure
from ..domain.keys import local_key
from ..domain.selection import FileSelection
from .paths import registry_path
from .sources import LocalSource, Source, source_from_registry

TOML_FIELDS = ("source", "key", "ref", "paths", "languages", "ignores", "max_bytes")
REGISTRY_HEADER = "# Projects tracked by delphi-code. Edit freely; add and remove rewrite this file without comments.\n"


@dataclass
class Entry:
    source: str
    key: str | None = None
    selection: FileSelection = field(default_factory=FileSelection)
    ref: str | None = None

    @cached_property
    def origin(self) -> Source:
        origin = source_from_registry(self.source)
        if origin is None:
            raise Failure("registry_invalid", f"Unsupported project source: {self.source}", ExitCode.USAGE)
        return origin

    @property
    def local_directory(self) -> Path | None:
        origin = self.origin
        return origin.path if isinstance(origin, LocalSource) else None

    def current_key(self) -> str:
        origin = self.origin
        if isinstance(origin, LocalSource) and not origin.path.is_dir():
            return self.key or local_key(origin.path)
        return origin.key

    @classmethod
    def from_toml(cls, data: dict) -> Entry:
        selection = FileSelection(
            **{name: data[name] for name in ("paths", "languages", "ignores", "max_bytes") if name in data}
        )
        return cls(data["source"], data.get("key"), selection, data.get("ref"))

    def to_toml(self) -> str:
        values = {"source": self.source, "key": self.key, "ref": self.ref, **vars(self.selection)}
        defaults = vars(FileSelection())
        return "".join(
            f"{name} = {json.dumps(values[name], ensure_ascii=False)}\n"
            for name in TOML_FIELDS
            if values[name] is not None and values[name] != defaults.get(name)
        )


class Registry:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else registry_path()

    def entries(self) -> list[Entry]:
        try:
            data = tomllib.loads(self.path.read_text())
        except FileNotFoundError:
            return []
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise Failure("registry_invalid", f"Cannot read {self.path}: {exc}", ExitCode.USAGE) from exc
        tables = data.get("repo", [])
        well_formed = isinstance(tables, list) and all(_is_well_formed_table(table) for table in tables)
        if set(data) - {"repo"} or not well_formed:
            raise Failure(
                "registry_invalid",
                f"{self.path}: expected [[repo]] tables with a source (an absolute path, bitbucket.org/workspace/repository, or github.com/owner/repository), optional key and ref strings, optional string lists paths, languages and ignores, and a positive integer max_bytes",
                ExitCode.USAGE,
            )
        return [Entry.from_toml(table) for table in tables]

    @contextmanager
    def edit(self) -> Iterator[list[Entry]]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_name(self.path.name + ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            original = self.entries()
            edited = copy.deepcopy(original)
            yield edited
            if edited != original:
                self._write(edited)

    def _write(self, entries: list[Entry]):
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(REGISTRY_HEADER + "".join("\n[[repo]]\n" + entry.to_toml() for entry in entries))
        temporary.replace(self.path)


def _is_well_formed_table(data) -> bool:
    return (
        isinstance(data, dict)
        and set(data) <= set(TOML_FIELDS)
        and isinstance(data.get("source"), str)
        and bool(data["source"].strip())
        and isinstance(data.get("key", ""), str)
        and isinstance(data.get("ref", ""), str)
        and all(_is_string_list(data[name]) for name in ("paths", "languages", "ignores") if name in data)
        and type(data.get("max_bytes", 1)) is int
        and data.get("max_bytes", 1) > 0
        and source_from_registry(data["source"]) is not None
    )


def _is_string_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)
