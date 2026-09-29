from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

if TYPE_CHECKING:
    from .model import LocalModel
    from .selection import FileSelection
    from .sources import Checkout

SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSION = 1
REVISION_FIELDS = ("ref", "commit")


class Manifest:
    def __init__(self, fields: dict):
        self._fields = fields

    @classmethod
    def placeholder(cls, key: str, project: Path | None) -> Manifest:
        return cls({"schema_version": SCHEMA_VERSION, "project": _optional_str(project), "source": {"key": key}, "ready": False})

    @classmethod
    def for_build(cls, checkout: Checkout, selection: FileSelection, model: LocalModel) -> Manifest:
        return cls({
            "schema_version": SCHEMA_VERSION, "project": _optional_str(checkout.project), "source": checkout.provenance,
            "ready": False, "model": str(model.directory), "model_sha256": model.sha256, **asdict(selection),
        })

    @classmethod
    def read_if_valid_json(cls, path: Path) -> Manifest | None:
        try:
            fields = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        return cls(fields) if isinstance(fields, dict) else None

    def write(self, path: Path):
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._fields, sort_keys=True) + "\n")
        temporary.replace(path)

    @property
    def schema_version(self) -> int | None:
        return self._fields.get("schema_version")

    @property
    def key(self) -> str | None:
        key = self._source.get("key")
        return key if isinstance(key, str) and key else None

    @property
    def project(self) -> Path | None:
        project = self._fields.get("project")
        return Path(project) if isinstance(project, str) and Path(project).is_absolute() else None

    @property
    def ready(self) -> bool:
        return self._fields.get("ready") is True

    @property
    def indexed_at(self) -> str | None:
        return self._fields.get("indexed_at")

    @property
    def revision(self) -> dict:
        return {name: self._source[name] for name in REVISION_FIELDS if name in self._source}

    def was_built_with(self, model: LocalModel) -> bool:
        return self._fields.get("model_sha256") == model.sha256

    def conflicts_with(self, model: LocalModel) -> bool:
        return self._fields.get("model_sha256") not in (None, model.sha256)

    def holds(self, commit: str, selection: FileSelection, model: LocalModel) -> bool:
        return (self.ready and self._source.get("commit") == commit and self.was_built_with(model)
                and all(self._fields.get(name) == value for name, value in asdict(selection).items()))

    def web_link(self, path: str, start_line: int, end_line: int) -> str | None:
        template = self._source.get("permalink")
        return template.format(path=quote(path), start=start_line, end=end_line) if template else None

    def completed(self) -> Manifest:
        return Manifest({**self._fields, "ready": True, "indexed_at": datetime.now(timezone.utc).isoformat()})

    def upgraded_with_key(self, key: str) -> Manifest:
        return Manifest({**self._fields, "schema_version": SCHEMA_VERSION, "source": {"key": key}})

    def to_json(self) -> dict:
        return dict(self._fields)

    @property
    def _source(self) -> dict:
        source = self._fields.get("source")
        return source if isinstance(source, dict) else {}


def _optional_str(path: Path | None) -> str | None:
    return str(path) if path else None
