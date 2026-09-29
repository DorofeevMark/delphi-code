"""The registry: repos.toml, the list of projects that sync keeps indexed."""
from contextlib import contextmanager
import copy
from dataclasses import dataclass, field
import fcntl
import json
from pathlib import Path
import tomllib

from .keys import local_key, project_key
from .model import Failure
from .paths import registry_path

DEFAULT_MAX_BYTES = 1_048_576
FIELDS = ("source", "key", "paths", "languages", "ignores", "max_bytes")


@dataclass
class Options:
    """Which files of a project are indexed."""
    paths: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    ignores: list[str] = field(default_factory=list)
    max_bytes: int = DEFAULT_MAX_BYTES


@dataclass
class Entry:
    source: str
    key: str | None = None
    options: Options = field(default_factory=Options)

    @property
    def path(self):
        path = Path(self.source).expanduser()
        return path.resolve() if path.is_absolute() else None

    def resolved_key(self):
        """The key of the checkout; a moved or deleted one keeps the key recorded when it was added."""
        path = self.path
        if path is None:
            return self.source
        if path.is_dir():
            return project_key(path)
        return self.key or local_key(path)

    @classmethod
    def parse(cls, data):
        strings = lambda value: isinstance(value, list) and all(isinstance(item, str) for item in value)
        valid = (isinstance(data, dict) and set(data) <= set(FIELDS)
                 and isinstance(data.get("source"), str) and data["source"].strip()
                 and isinstance(data.get("key", ""), str)
                 and all(strings(data[name]) for name in ("paths", "languages", "ignores") if name in data)
                 and type(data.get("max_bytes", 1)) is int and data.get("max_bytes", 1) > 0)
        if not valid:
            return None
        options = Options(**{name: data[name] for name in ("paths", "languages", "ignores", "max_bytes") if name in data})
        return cls(data["source"], data.get("key"), options)

    def serialize(self):
        """TOML lines for this entry, leaving out unset keys and default options."""
        values = {"source": self.source, "key": self.key, **vars(self.options)}
        defaults = vars(Options())
        return "".join(f"{name} = {json.dumps(values[name], ensure_ascii=False)}\n"
                       for name in FIELDS if values[name] is not None and values[name] != defaults.get(name))


class Registry:
    def __init__(self, path=None):
        self.path = Path(path) if path else registry_path()

    def entries(self):
        try:
            data = tomllib.loads(self.path.read_text())
        except FileNotFoundError:
            return []
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise Failure("registry_invalid", f"Cannot read {self.path}: {exc}", 2) from exc
        repos = data.get("repo", [])
        entries = [Entry.parse(item) for item in repos] if isinstance(repos, list) else [None]
        if set(data) - {"repo"} or None in entries:
            raise Failure("registry_invalid", f"{self.path}: expected [[repo]] tables with a source string, an optional key string, optional string lists paths, languages and ignores, and a positive integer max_bytes", 2)
        return entries

    @contextmanager
    def edit(self):
        """Yields the entries as a list to change in place; saves it only if it changed."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_name(self.path.name + ".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            original = self.entries()
            entries = copy.deepcopy(original)
            yield entries
            if entries != original:
                self._save(entries)

    def _save(self, entries):
        text = "# Projects tracked by delphi-code. Edit freely; add and remove rewrite this file without comments.\n"
        text += "".join("\n[[repo]]\n" + entry.serialize() for entry in entries)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(text)
        temporary.replace(self.path)
