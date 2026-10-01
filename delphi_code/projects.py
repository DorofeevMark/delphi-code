from __future__ import annotations

import asyncio
from pathlib import Path
import shlex

from .errors import ExitCode, Failure
from .keys import match_key
from .manifest import Manifest
from .model import LocalModel
from .progress import Progress
from .registry import Entry, Registry
from .selection import FileSelection
from .sources import Checkout, GitRemoteSource, LocalSource, Source, source_from_argument
from .store import Index, ResolvedProject, Store, require_sqlite_extensions


class SearchRequest:
    def __init__(self, query: str, paths: list[str], languages: list[str], limit: int):
        if not query.strip():
            raise Failure("usage", "Query must be nonempty", ExitCode.USAGE)
        self.query = query
        self.paths = paths
        self.languages = languages
        self.limit = limit


def open_model(location: str | Path | None) -> LocalModel:
    model = LocalModel.inspect(location)
    require_sqlite_extensions()
    return model


def resolve_existing(store: Store, name: str) -> ResolvedProject:
    resolved = store.resolve(name)
    directory_exists = resolved.project is not None and resolved.project.is_dir()
    if not directory_exists and resolved.index is None:
        raise Failure("project_missing", f"Project directory does not exist: {resolved.project}", ExitCode.USAGE)
    return resolved


def resolve_indexed(store: Store, name: str) -> tuple[str, Index]:
    project, key, index = resolve_existing(store, name)
    if index is None:
        raise Failure(
            "index_missing",
            f"No index exists; run delphi-code index -p {shlex.quote(str(project))}",
            ExitCode.INDEX_STATE,
        )
    return key, index


def index_local_project(store: Store, name: str, selection: FileSelection, model_location: str) -> dict:
    project, key, index = store.resolve(name)
    if index and index.project is None:
        raise Failure("usage", f"{key} is a remote repository; update it with delphi-code sync", ExitCode.USAGE)
    if project is None or not project.is_dir():
        raise Failure("project_missing", f"Project directory does not exist: {project}", ExitCode.USAGE)
    model = open_model(model_location)
    checkout = Checkout(project, project, {"kind": "local", "key": key})
    with Progress.on_terminal() as progress:
        progress.begin(key)
        result = index_checkout(store, checkout, selection, model, progress)
        progress.end("✓", _indexed_outcome(result))
    return result


def index_checkout(
    store: Store, checkout: Checkout, selection: FileSelection, model: LocalModel, progress: Progress
) -> dict:
    from .files import collect
    from .indexing import build_index

    index = store.get_or_create(checkout.key, checkout.project)
    with index.lock(True):
        index.read_manifest_compatible_with(model)
        progress.stage("reading files")
        collected = collect(checkout.directory, selection, excluded=model.directory)
        manifest = Manifest.for_build(checkout, selection, model)
        index.write_manifest(manifest)
        total = len(collected.files)
        progress.count("embedding", 0, total, "files")
        incremental = asyncio.run(
            build_index(
                index.directory,
                collected.files,
                model,
                on_files_done=lambda done: progress.count("embedding", done, total, "files"),
            )
        )
        progress.stage("saving")
        manifest = manifest.completed()
        counts = index.counts()
        index.write_manifest(manifest)
    return {
        "project": _path_text(manifest.project),
        "key": checkout.key,
        "index_directory": str(index.directory),
        **manifest.revision,
        **counts,
        "skipped": collected.skipped,
        "incremental": incremental,
    }


def status(store: Store, name: str) -> dict:
    key, index = resolve_indexed(store, name)
    with index.lock(False):
        manifest = index.read_manifest()
        return {
            "key": key,
            "index_directory": str(index.directory),
            **manifest.to_json(),
            **(index.counts() if manifest.ready else {}),
        }


def search_project(store: Store, name: str, request: SearchRequest, model_location: str) -> dict:
    key, index = resolve_indexed(store, name)
    model = open_model(model_location)
    with index.lock(False):
        manifest = index.read_searchable_manifest(model)
        results = _search_index(index, manifest, request, _query_vector(model, request))
    return {
        "project": _path_text(manifest.project),
        "key": key,
        "index_directory": str(index.directory),
        **manifest.revision,
        "query": request.query,
        "results": results,
    }


def search_everywhere(store: Store, request: SearchRequest, model_location: str) -> dict:
    indexes, unrecognized = store.all(), store.unrecognized_directories()
    if not indexes and not unrecognized:
        raise Failure(
            "index_missing",
            "No indexes exist; run delphi-code index -p /absolute/path/to/project first",
            ExitCode.INDEX_STATE,
        )
    if unrecognized:
        raise Failure(
            "index_incompatible", f"{unrecognized[0]}: Index has no valid project identity", ExitCode.INDEX_STATE
        )
    model = open_model(model_location)
    query_vector = _query_vector(model, request)
    results, projects = [], []
    for index in indexes:
        try:
            with index.lock(False):
                manifest = index.read_searchable_manifest(model)
                project = _path_text(manifest.project)
                results.extend(
                    {**row, "project": project, "key": index.key}
                    for row in _search_index(index, manifest, request, query_vector)
                )
                projects.append(project or index.key)
        except Failure as exc:
            raise Failure(exc.code, f"{index.directory}: {exc}", exc.exit_code) from exc
    results.sort(
        key=lambda row: (row["distance"], row["key"], row["path"], row["start_line"], row["end_line"], row["text"])
    )
    return {"project": None, "projects": sorted(projects), "query": request.query, "results": results[: request.limit]}


def list_projects(store: Store, registry: Registry) -> dict:
    tracked = {entry.current_key(): entry for entry in registry.entries()}
    stored = {index.key: index for index in store.all()}
    rows = []
    for key in sorted(set(tracked) | set(stored)):
        index, entry = stored.get(key), tracked.get(key)
        manifest = index.manifest if index else None
        rows.append(
            {
                "key": key,
                "tracked": entry is not None,
                "source": entry.source if entry else None,
                "project": _path_text(manifest.project) if manifest else None,
                "index_directory": str(index.directory) if index else None,
                "ready": manifest.ready if manifest else False,
                "indexed_at": manifest.indexed_at if manifest else None,
                **(manifest.revision if manifest else {}),
            }
        )
    return {"registry": str(registry.path), "repos": rows}


def remove_project(store: Store, registry: Registry, name: str, keep_index: bool) -> dict:
    with registry.edit() as entries:
        keys = [entry.current_key() for entry in entries]
        candidates = [
            *zip(keys, (entry.local_directory for entry in entries), strict=True),
            *((index.key, index.project) for index in store.all()),
        ]
        key = match_key(name, candidates)
        if key is None:
            raise Failure("project_missing", f"No tracked project or index matches {name}", ExitCode.USAGE)
        untracked = key in keys
        entries[:] = [entry for candidate, entry in zip(keys, entries, strict=True) if candidate != key]
        deleted = None if keep_index else _delete_index(store, key)
    return {"key": key, "untracked": untracked, "deleted_index": deleted}


def add_sources(
    store: Store,
    registry: Registry,
    arguments: list[str],
    ref: str | None,
    selection: FileSelection,
    model_location: str,
    sync_now: bool,
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
    return sync(store, registry, added, model_location)


def add_picked_repositories(
    store: Store, registry: Registry, ref: str | None, selection: FileSelection, model_location: str, sync_now: bool
) -> dict:
    from .picker import RepositoryPicker

    tracked = {entry.current_key() for entry in registry.entries()}
    changes = RepositoryPicker().choose(tracked)
    if changes is None:
        return {"registry": str(registry.path), "cancelled": True, "repos": [], "removed": []}
    added = [Entry(key, selection=selection, ref=ref) for key in changes.added]
    with registry.edit() as entries:
        kept = [entry for entry in entries if entry.current_key() not in changes.removed]
        entries[:] = kept + added
    removed = [{"key": key, "deleted_index": _delete_index(store, key)} for key in changes.removed]
    if not sync_now:
        return {"registry": str(registry.path), "cancelled": False, "repos": _summaries(added), "removed": removed}
    never_indexed = [entry for entry in kept if not _has_ready_index(store, entry)]
    try:
        return {**sync(store, registry, never_indexed + added, model_location), "cancelled": False, "removed": removed}
    except Failure as failure:
        failure.data = {**(failure.data or {}), "cancelled": False, "removed": removed}
        raise


def sync(store: Store, registry: Registry, entries: list[Entry], model_location: str) -> dict:
    if not entries:
        return {"registry": str(registry.path), "repos": []}
    model = open_model(model_location)
    results = []
    with Progress.on_terminal() as progress:
        for position, entry in enumerate(entries, start=1):
            progress.begin(f"[{position}/{len(entries)}] {entry.current_key()}")
            try:
                result = _sync_entry(store, entry, model, progress)
                results.append({"source": entry.source, "ok": True, **result})
                progress.end("✓", "unchanged" if result["unchanged"] else _indexed_outcome(result))
            except Exception as exc:
                failure = Failure.from_exception(exc)
                results.append({"source": entry.source, "ok": False, "error": failure.to_json()})
                progress.end("✗", str(failure))
    data = {"registry": str(registry.path), "repos": results}
    failed = [result["source"] for result in results if not result["ok"]]
    if failed:
        raise Failure(
            "sync_failed",
            f"{len(failed)} of {len(results)} tracked projects failed: {', '.join(failed)}",
            ExitCode.OPERATION,
            data,
        )
    return data


def _sync_entry(store: Store, entry: Entry, model: LocalModel, progress: Progress) -> dict:
    origin = entry.origin
    progress.stage("checking for changes")
    revision = origin.latest_revision(entry.ref)
    index = store.get(origin.key) if revision else None
    if revision and index and index.manifest.holds(revision.commit, entry.selection, model):
        return {
            "unchanged": True,
            "project": None,
            "key": index.key,
            "index_directory": str(index.directory),
            **index.manifest.revision,
        }
    if isinstance(origin, GitRemoteSource):
        progress.stage("cloning")
    with origin.checkout(entry.ref, revision) as checkout:
        return {"unchanged": False, **index_checkout(store, checkout, entry.selection, model, progress)}


def _indexed_outcome(result: dict) -> str:
    return f"{result.get('files', 0)} files, {result.get('chunks', 0)} chunks"


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


def _query_vector(model: LocalModel, request: SearchRequest) -> bytes:
    return model.embed([request.query])[0].tobytes()


def _search_index(index: Index, manifest: Manifest, request: SearchRequest, query_vector: bytes) -> list[dict]:
    rows = index.search(query_vector, request.paths, request.languages, request.limit)
    for row in rows:
        if link := manifest.web_link(row["path"], row["start_line"], row["end_line"]):
            row["url"] = link
    return rows


def _path_text(path: Path | None) -> str | None:
    return str(path) if path else None
