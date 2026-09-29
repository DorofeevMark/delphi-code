import argparse
import asyncio
from contextlib import redirect_stdout
from importlib.metadata import version
import json
import os
from pathlib import Path
import shlex
import sqlite3
import sys
import tempfile
from urllib.parse import quote
from datetime import datetime, timezone

from .model import Failure, embed, inspect_model, load_model
from .keys import match_key
from .paths import model_directory
from .registry import DEFAULT_MAX_BYTES, Entry, FileSelection, Registry
from .sources import Checkout, LocalSource, source_from_argument
from .store import SCHEMA_VERSION, Store


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Failure("usage", message, 2)


def selection_options(command, indexing):
    command.add_argument("--path", action="append", default=[], help="Project-relative glob; repeat for alternatives")
    command.add_argument("--language", action="append", default=[])
    if indexing:
        command.add_argument("--ignore", action="append", default=[])
        command.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)


def model_option(command):
    command.add_argument("--model", default=os.environ.get("DELPHI_CODE_MODEL") or model_directory())


def arguments():
    parser = Parser(prog="delphi-code", description="Offline local code search")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("index", "search", "status", "doctor"):
        command = commands.add_parser(name)
        command.add_argument("--project", "-p", default=None if name == "search" else str(Path.cwd()), help="Project path, key, or indexed name; search defaults to all indexes")
        if name != "status":
            model_option(command)
        if name in {"index", "search"}:
            selection_options(command, name == "index")
        if name == "search":
            command.add_argument("query")
            command.add_argument("--limit", type=int, default=10)
    add = commands.add_parser("add", help="Track projects in the registry and index them")
    add.add_argument("sources", nargs="+", metavar="SOURCE", help="Project directory, or bitbucket.org/workspace/repository")
    add.add_argument("--ref", help="Branch or tag of remote repositories; defaults to the default branch")
    selection_options(add, True)
    model_option(add)
    add.add_argument("--no-sync", action="store_true", help="Only update the registry")
    sync = commands.add_parser("sync", help="Index every tracked project")
    model_option(sync)
    commands.add_parser("list", help="List tracked projects and stored indexes")
    remove = commands.add_parser("remove", help="Stop tracking a project and delete its index")
    remove.add_argument("name", help="Project path, key, or indexed name")
    remove.add_argument("--keep-index", action="store_true", help="Only untrack the project")
    setup = commands.add_parser("setup", help="Download or import the pinned model and check the installation")
    setup.add_argument("--from", dest="source", help="Import a prepared MiniLM model without network access")
    setup.add_argument("--model", default=os.environ.get("DELPHI_CODE_MODEL") or model_directory(), help="Model destination")
    return parser.parse_args()


def check_runtime(model):
    model_path, identity = inspect_model(model)
    if not hasattr(sqlite3.Connection, "enable_load_extension"):
        raise Failure("sqlite_extensions_unavailable", "This Python disables SQLite extension loading; provision a Python build with loadable SQLite extensions", 3)
    return model_path, identity


def as_failure(exc):
    if isinstance(exc, Failure):
        return exc
    missing = isinstance(exc, ModuleNotFoundError)
    return Failure("dependency_missing" if missing else "runtime_error", str(exc), 3 if missing else 5)


def index_project(store, checkout, selection, model_path, identity, load):
    from .files import collect
    from .indexing import run

    key = checkout.provenance["key"]
    index = store.get_or_create(key, checkout.project)
    with index.lock(True):
        if index.read_manifest().get("model_sha256") not in (None, identity):
            raise Failure("model_mismatch", f"Model assets differ from the index; restore the original model or move {index.directory} aside and reindex", 4)
        model = load()
        sources, skipped = collect(checkout.directory, selection.paths, selection.languages, selection.ignores, model_path, selection.max_bytes)
        info = {
            "schema_version": SCHEMA_VERSION, "project": str(checkout.project) if checkout.project else None, "source": checkout.provenance,
            "ready": False, "model": str(model_path), "model_sha256": identity, **vars(selection),
        }
        index.write_manifest(info)
        stats = asyncio.run(run(index.directory, sources, model, identity))
        info.update(ready=True, indexed_at=datetime.now(timezone.utc).isoformat())
        total = index.counts()
        index.write_manifest(info)
    return {"project": info["project"], "key": key, "index_directory": str(index.directory), **revision_fields(info),
            **total, "skipped": skipped, "incremental": stats}


def revision_fields(info):
    source = info.get("source") or {}
    return {name: source[name] for name in ("ref", "commit") if name in source}


def index_holds_commit(info, commit, selection, identity):
    return (info.get("ready") is True and (info.get("source") or {}).get("commit") == commit
            and info.get("model_sha256") == identity and all(info.get(name) == value for name, value in vars(selection).items()))


def loader(model_path):
    loaded = []

    def load():
        if not loaded:
            loaded.append(load_model(model_path))
        return loaded[0]
    return load


def execute(args):
    if args.command == "setup":
        from .setup import provision

        return provision(args)
    store = Store()
    if args.command in {"add", "sync", "list", "remove"}:
        return manage(args, store, Registry())
    if args.command == "search" and (not args.query.strip() or not 1 <= args.limit <= 1000):
        raise Failure("usage", "Query must be nonempty and --limit must be between 1 and 1000", 2)
    if args.command == "search" and args.project is None:
        return search_all(args, store)
    project, key, index = store.resolve(args.project)
    if args.command == "index" and index and index.project is None:
        raise Failure("usage", f"{key} is a remote repository; update it with delphi-code sync", 2)
    exists = project is not None and project.is_dir()
    if not exists and (index is None or args.command == "index"):
        raise Failure("project_missing", f"Project directory does not exist: {project}", 2)
    if args.command in {"search", "status"} and index is None:
        raise Failure("index_missing", f"No index exists; run delphi-code index -p {shlex.quote(str(project))}", 4)
    if args.command == "status":
        with index.lock(False):
            info = index.read_manifest()
            return {"key": key, "index_directory": str(index.directory), **info, **(index.counts() if info["ready"] else {})}
    if args.command == "index" and args.max_bytes < 1:
        raise Failure("usage", "--max-bytes must be positive", 2)
    model_path, identity = check_runtime(args.model)
    if args.command == "doctor":
        import cocoindex
        import sqlite_vec

        from .indexing import check_storage

        with tempfile.TemporaryDirectory(prefix="delphi-code-doctor-") as scratch:
            asyncio.run(check_storage(Path(scratch)))
        model = load_model(model_path)
        vector = embed(model, ["local code search"])[0]
        db = sqlite3.connect(":memory:")
        try:
            db.enable_load_extension(True)
            sqlite_vec.load(db)
            db.enable_load_extension(False)
            distance = db.execute("SELECT vec_distance_L2(?, ?)", (vector.tobytes(), vector.tobytes())).fetchone()[0]
        finally:
            db.close()
        return {
            "project": str(project) if project else None, "key": key, "index_directory": str(index.directory) if index else None, "model": str(model_path), "model_sha256": identity,
            "dimensions": len(vector), "self_distance": distance, "device": "cpu",
            "cocoindex_storage": "ok", "offline": True, "network_guard": "python_audit", "sqlite": sqlite3.sqlite_version,
            "dependencies": {name: version(name) for name in ("cocoindex", "sqlite-vec", "sentence-transformers", "torch")},
        }
    if args.command == "index":
        selection = FileSelection(args.path, args.language, args.ignore, args.max_bytes)
        checkout = Checkout(project, project, {"kind": "local", "key": key})
        return index_project(store, checkout, selection, model_path, identity, loader(model_path))
    with index.lock(False):
        previous = index.read_manifest()
        if previous.get("model_sha256") not in (None, identity):
            raise Failure("model_mismatch", f"Model assets differ from the index; restore the original model or move {index.directory} aside and reindex", 4)
        if not previous["ready"]:
            raise Failure("index_incomplete", "Last index did not complete; run index again before searching", 4)
        model = load_model(model_path)
        vector = embed(model, [args.query])[0].tobytes()
        return {"project": previous["project"], "key": key, "index_directory": str(index.directory), **revision_fields(previous),
                "query": args.query, "results": search_rows(index, args, vector)}


def manage(args, store, registry):
    if args.command == "list":
        tracked = {entry.current_key(): entry for entry in registry.entries()}
        stored = {index.key: index for index in store.all()}
        rows = []
        for key in sorted(set(tracked) | set(stored)):
            index, entry = stored.get(key), tracked.get(key)
            info = index.info if index else {}
            rows.append({"key": key, "tracked": entry is not None, "source": entry.source if entry else None,
                         "project": info.get("project"), "index_directory": str(index.directory) if index else None,
                         "ready": info.get("ready", False), "indexed_at": info.get("indexed_at"), **revision_fields(info)})
        return {"registry": str(registry.path), "repos": rows}
    if args.command == "remove":
        with registry.edit() as entries:
            keys = [entry.current_key() for entry in entries]
            indexes = store.all()
            key = match_key(args.name, [*((key, entry.local_directory) for key, entry in zip(keys, entries)),
                                     *((index.key, index.project) for index in indexes)])
            if key is None:
                raise Failure("project_missing", f"No tracked project or index matches {args.name}", 2)
            untracked = key in keys
            entries[:] = [entry for candidate, entry in zip(keys, entries) if candidate != key]
            index = None if args.keep_index else next((index for index in indexes if index.key == key), None)
            if index:
                store.delete(index)
        return {"key": key, "untracked": untracked, "deleted_index": str(index.directory) if index else None}
    if args.command == "add":
        if args.max_bytes < 1:
            raise Failure("usage", "--max-bytes must be positive", 2)
        added = []
        selection = FileSelection(args.path, args.language, args.ignore, args.max_bytes)
        with registry.edit() as entries:
            keys = [entry.current_key() for entry in entries]
            for raw in args.sources:
                origin = source_from_argument(raw)
                if isinstance(origin, LocalSource):
                    if not origin.path.is_dir():
                        raise Failure("project_missing", f"Project directory does not exist: {origin.path}", 2)
                    if args.ref:
                        raise Failure("usage", f"--ref applies only to remote repositories, not {origin.path}", 2)
                    entry = Entry(str(origin.path), origin.key, selection)
                else:
                    entry = Entry(origin.key, selection=selection, ref=args.ref)
                if entry.current_key() in keys:
                    entries[keys.index(entry.current_key())] = entry
                else:
                    entries.append(entry)
                    keys.append(entry.current_key())
                added.append(entry)
        if args.no_sync:
            return {"registry": str(registry.path), "repos": [{"source": entry.source, "key": entry.current_key()} for entry in added]}
        return sync(added, args.model, store, registry)
    return sync(registry.entries(), args.model, store, registry)


def sync(entries, model, store, registry):
    model_path, identity = check_runtime(model) if entries else (None, None)
    load = loader(model_path)
    results = []
    for entry in entries:
        try:
            origin = entry.origin
            revision = origin.latest_revision(entry.ref)
            index = store.get(origin.key) if revision else None
            if index and index_holds_commit(index.info, revision.commit, entry.selection, identity):
                results.append({"source": entry.source, "ok": True, "unchanged": True, "project": None, "key": index.key,
                                "index_directory": str(index.directory), **revision_fields(index.info)})
                continue
            with origin.checkout(entry.ref, revision) as checkout:
                result = index_project(store, checkout, entry.selection, model_path, identity, load)
            results.append({"source": entry.source, "ok": True, "unchanged": False, **result})
        except Exception as exc:
            failure = as_failure(exc)
            results.append({"source": entry.source, "ok": False, "error": {"code": failure.code, "message": str(failure)}})
    data = {"registry": str(registry.path), "repos": results}
    failed = [result["source"] for result in results if not result["ok"]]
    if failed:
        raise Failure("sync_failed", f"{len(failed)} of {len(results)} tracked projects failed: {', '.join(failed)}", 5, data)
    return data


def search_rows(index, args, vector):
    conditions, parameters = [], [vector]
    if args.language:
        conditions.append("language IN (" + ",".join("?" for _ in args.language) + ")")
        parameters.extend(args.language)
    if args.path:
        conditions.append("(" + " OR ".join("path_matches(path, ?)" for _ in args.path) + ")")
        parameters.extend(args.path)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    with index.database() as db:
        import fnmatch

        db.create_function("path_matches", 2, fnmatch.fnmatchcase, deterministic=True)
        rows = db.execute(
            "SELECT path, language, text, start_line, end_line, vec_distance_L2(vector, ?) AS distance "
            "FROM passages" + where + " ORDER BY distance, path, start_line, id LIMIT ?",
            [*parameters, args.limit],
        ).fetchall()
    permalink = (index.info.get("source") or {}).get("permalink")
    return [
        {**dict(row), "score": max(-1.0, min(1.0, 1.0 - row["distance"] ** 2 / 2.0)),
         **({"url": permalink.format(path=quote(row["path"]), start=row["start_line"], end=row["end_line"])} if permalink else {})}
        for row in rows
    ]


def search_all(args, store):
    store.all()
    directories = sorted(path for path in store.root.glob("*") if path.is_dir())
    if not directories:
        raise Failure("index_missing", "No indexes exist; run delphi-code index -p /absolute/path/to/project first", 4)
    model_path, identity = check_runtime(args.model)
    model = load_model(model_path)
    vector = embed(model, [args.query])[0].tobytes()
    indexes = {index.directory: index for index in store.all()}
    results, projects = [], []
    for directory in directories:
        try:
            index = indexes.get(directory)
            if index is None:
                raise Failure("index_incompatible", "Index has no valid project identity", 4)
            with index.lock(False):
                info = index.read_manifest()
                if not info.get("ready"):
                    raise Failure("index_incomplete", "Last index did not complete; run index again", 4)
                if info.get("model_sha256") != identity:
                    raise Failure("model_mismatch", "Index uses different model assets; select a compatible project with --project", 4)
                results.extend({**row, "project": info["project"], "key": index.key} for row in search_rows(index, args, vector))
                projects.append(info["project"] or index.key)
        except Failure as exc:
            raise Failure(exc.code, f"{directory}: {exc}", exc.exit_code) from exc
    results.sort(key=lambda row: (row["distance"], row["key"], row["path"], row["start_line"], row["end_line"], row["text"]))
    return {"project": None, "projects": sorted(projects), "query": args.query, "results": results[:args.limit]}


def main():
    command = None
    exit_code = 0
    try:
        args = arguments()
        command = args.command
        with redirect_stdout(sys.stderr):
            result = execute(args)
        payload = {"schema_version": 1, "ok": True, "command": command, "data": result}
    except Exception as exc:
        if os.environ.get("DELPHI_CODE_DEBUG") == "1":
            import traceback

            traceback.print_exc(file=sys.stderr)
        failure = as_failure(exc)
        exit_code = failure.exit_code
        print(f"delphi-code: {failure}", file=sys.stderr)
        payload = {"schema_version": 1, "ok": False, "command": command,
                   "error": {"code": failure.code, "message": str(failure)}}
        if failure.data is not None:
            payload["data"] = failure.data
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False))
    raise SystemExit(exit_code)
