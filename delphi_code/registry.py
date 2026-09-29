from contextlib import contextmanager
import copy
from dataclasses import dataclass, field
from functools import cached_property
import fcntl
import json
from pathlib import Path
import tomllib

from .keys import local_key
from .errors import ExitCode, Failure
from .paths import registry_path
from .sources import LocalSource, source_from_registry

DEFAULT_MAX_BYTES = 1_048_576
TOML_FIELDS = ("source", "key", "ref", "paths", "languages", "ignores", "max_bytes")
REGISTRY_HEADER = "# Projects tracked by delphi-code. Edit freely; add and remove rewrite this file without comments.\n"


@dataclass
class FileSelection:
    paths: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    ignores: list[str] = field(default_factory=list)
    max_bytes: int = DEFAULT_MAX_BYTES


@dataclass
class Entry:
    source: str
    key: str | None = None
    selection: FileSelection = field(default_factory=FileSelection)
    ref: str | None = None

    @cached_property
    def origin(self):
        return source_from_registry(self.source)

    @property
    def local_directory(self):
        return self.origin.path if isinstance(self.origin, LocalSource) else None

    def current_key(self):
        checkout_moved_or_deleted = isinstance(self.origin, LocalSource) and not self.origin.path.is_dir()
        if checkout_moved_or_deleted:
            return self.key or local_key(self.origin.path)
        return self.origin.key

    @classmethod
    def from_toml(cls, data):
        is_string_list = lambda value: isinstance(value, list) and all(isinstance(item, str) for item in value)
        valid = (isinstance(data, dict) and set(data) <= set(TOML_FIELDS)
                 and isinstance(data.get("source"), str) and data["source"].strip()
                 and isinstance(data.get("key", ""), str) and isinstance(data.get("ref", ""), str)
                 and all(is_string_list(data[name]) for name in ("paths", "languages", "ignores") if name in data)
                 and type(data.get("max_bytes", 1)) is int and data.get("max_bytes", 1) > 0)
        if not valid or source_from_registry(data["source"]) is None:
            return None
        selection = FileSelection(**{name: data[name] for name in ("paths", "languages", "ignores", "max_bytes") if name in data})
        return cls(data["source"], data.get("key"), selection, data.get("ref"))

    def to_toml(self):
        values = {"source": self.source, "key": self.key, "ref": self.ref, **vars(self.selection)}
        defaults = vars(FileSelection())
        return "".join(f"{name} = {json.dumps(values[name], ensure_ascii=False)}\n"
                       for name in TOML_FIELDS if values[name] is not None and values[name] != defaults.get(name))


class Registry:
    def __init__(self, path=None):
        self.path = Path(path) if path else registry_path()

    def entries(self):
        try:
            data = tomllib.loads(self.path.read_text())
        except FileNotFoundError:
            return []
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise Failure("registry_invalid", f"Cannot read {self.path}: {exc}", ExitCode.USAGE) from exc
        tables = data.get("repo", [])
        entries = [Entry.from_toml(table) for table in tables] if isinstance(tables, list) else [None]
        if set(data) - {"repo"} or None in entries:
            raise Failure("registry_invalid", f"{self.path}: expected [[repo]] tables with a source (an absolute path, bitbucket.org/workspace/repository, or github.com/owner/repository), optional key and ref strings, optional string lists paths, languages and ignores, and a positive integer max_bytes", ExitCode.USAGE)
        return entries

    @contextmanager
    def edit(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_name(self.path.name + ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            original = self.entries()
            edited = copy.deepcopy(original)
            yield edited
            if edited != original:
                self._write(edited)

    def _write(self, entries):
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(REGISTRY_HEADER + "".join("\n[[repo]]\n" + entry.to_toml() for entry in entries))
        temporary.replace(self.path)
