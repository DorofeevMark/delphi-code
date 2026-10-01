from ..domain.errors import ExitCode, Failure
from ..domain.keys import match_key
from ..domain.selection import FileSelection
from ..domain.tracking_changes import TrackingChanges
from ..infrastructure.project_identity import project_key
from ..infrastructure.registry import Entry, Registry
from ..infrastructure.sources import LocalSource, Source, source_from_argument
from ..infrastructure.store import Store
from .indexing import sync
from .progress import SILENT, Progress


def add_sources(
    store: Store,
    registry: Registry,
    arguments: list[str],
    ref: str | None,
    selection: FileSelection,
    model_location: str,
    sync_now: bool,
    progress: Progress = SILENT,
) -> dict:
    added = []
    with registry.edit() as entries:
        keys = [entry.current_key() for entry in entries]
        for argument in arguments:
            entry = _entry_for(source_from_argument(argument), ref, selection)
            if entry.current_key() in keys:
                entries[keys.index(entry.current_key())] = entry
            else:
                entries.append(entry)
                keys.append(entry.current_key())
            added.append(entry)
    if not sync_now:
        return {"registry": str(registry.path), "repos": _summaries(added)}
    return sync(store, registry, added, model_location, progress)


def apply_tracking_changes(
    store: Store,
    registry: Registry,
    changes: TrackingChanges,
    ref: str | None,
    selection: FileSelection,
    model_location: str,
    sync_now: bool,
    progress: Progress = SILENT,
) -> dict:
    added = [Entry(key, selection=selection, ref=ref) for key in changes.added]
    with registry.edit() as entries:
        kept = [entry for entry in entries if entry.current_key() not in changes.removed]
        entries[:] = kept + added
    removed = [{"key": key, "deleted_index": _delete_index(store, key)} for key in changes.removed]
    if not sync_now:
        return {"registry": str(registry.path), "repos": _summaries(added), "removed": removed}
    never_indexed = [entry for entry in kept if not _has_ready_index(store, entry)]
    try:
        return {**sync(store, registry, never_indexed + added, model_location, progress), "removed": removed}
    except Failure as failure:
        failure.data = {**(failure.data or {}), "removed": removed}
        raise


def remove_project(store: Store, registry: Registry, name: str, keep_index: bool) -> dict:
    with registry.edit() as entries:
        keys = [entry.current_key() for entry in entries]
        candidates = [
            *zip(keys, (entry.local_directory for entry in entries), strict=True),
            *((index.key, index.project) for index in store.all()),
        ]
        key = match_key(name, candidates, project_key)
        if key is None:
            raise Failure("project_missing", f"No tracked project or index matches {name}", ExitCode.USAGE)
        untracked = key in keys
        entries[:] = [entry for candidate, entry in zip(keys, entries, strict=True) if candidate != key]
        deleted = None if keep_index else _delete_index(store, key)
    return {"key": key, "untracked": untracked, "deleted_index": deleted}


def _entry_for(origin: Source, ref: str | None, selection: FileSelection) -> Entry:
    if not isinstance(origin, LocalSource):
        return Entry(origin.key, selection=selection, ref=ref)
    if not origin.path.is_dir():
        raise Failure("project_missing", f"Project directory does not exist: {origin.path}", ExitCode.USAGE)
    if ref:
        raise Failure("usage", f"--ref applies only to remote repositories, not {origin.path}", ExitCode.USAGE)
    return Entry(str(origin.path), origin.key, selection)


def _has_ready_index(store: Store, entry: Entry) -> bool:
    index = store.get(entry.current_key())
    return index is not None and index.manifest.ready


def _summaries(entries: list[Entry]) -> list[dict]:
    return [{"source": entry.source, "key": entry.current_key()} for entry in entries]


def _delete_index(store: Store, key: str) -> str | None:
    index = store.get(key)
    if index is None:
        return None
    store.delete(index)
    return str(index.directory)
