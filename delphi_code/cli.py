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
from datetime import datetime, timezone

from .model import Failure, embed, inspect_model, load_model
from .keys import project_key, select
from .paths import model_directory
from .registry import DEFAULT_MAX_BYTES, Entry, Options, Registry
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
    add.add_argument("sources", nargs="+", metavar="SOURCE", help="Project directory")
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


def index_project(store, project, key, options, model_path, identity, load):
    from .files import collect
    from .indexing import run

    index = store.create(key, project)
    with index.lock(True):
        if index.manifest().get("model_sha256") not in (None, identity):
            raise Failure("model_mismatch", f"Model assets differ from the index; restore the original model or move {index.directory} aside and reindex", 4)
        model = load()
        sources, skipped = collect(project, options.paths, options.languages, options.ignores, model_path, options.max_bytes)
        info = {
            "schema_version": SCHEMA_VERSION, "project": str(project), "source": {"key": key}, "ready": False,
            "model": str(model_path), "model_sha256": identity, **vars(options),
        }
        index.write(info)
        stats = asyncio.run(run(index.directory, sources, model, identity))
        info.update(ready=True, indexed_at=datetime.now(timezone.utc).isoformat())
        total = index.counts()
        index.write(info)
    return {"project": str(project), "key": key, "index_directory": str(index.directory), **total, "skipped": skipped, "incremental": stats}


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
    exists = project is not None and project.is_dir()
    if not exists and (index is None or args.command == "index"):
        raise Failure("project_missing", f"Project directory does not exist: {project}", 2)
    if args.command in {"search", "status"} and index is None:
        raise Failure("index_missing", f"No index exists; run delphi-code index -p {shlex.quote(str(project))}", 4)
    if args.command == "status":
        with index.lock(False):
            info = index.manifest()
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
            "project": str(project), "key": key, "index_directory": str(index.directory) if index else None, "model": str(model_path), "model_sha256": identity,
            "dimensions": len(vector), "self_distance": distance, "device": "cpu",
            "cocoindex_storage": "ok", "offline": True, "network_guard": "python_audit", "sqlite": sqlite3.sqlite_version,
            "dependencies": {name: version(name) for name in ("cocoindex", "sqlite-vec", "sentence-transformers", "torch")},
        }
    if args.command == "index":
        options = Options(args.path, args.language, args.ignore, args.max_bytes)
        return index_project(store, project, key, options, model_path, identity, loader(model_path))
    with index.lock(False):
        previous = index.manifest()
        if previous.get("model_sha256") not in (None, identity):
            raise Failure("model_mismatch", f"Model assets differ from the index; restore the original model or move {index.directory} aside and reindex", 4)
        if not previous["ready"]:
            raise Failure("index_incomplete", "Last index did not complete; run index again before searching", 4)
        model = load_model(model_path)
        vector = embed(model, [args.query])[0].tobytes()
        return {"project": previous["project"], "key": key, "index_directory": str(index.directory), "query": args.query,
                "results": search_rows(index, args, vector)}


def manage(args, store, registry):
    if args.command == "list":
        tracked = {entry.resolved_key(): entry for entry in registry.entries()}
        stored = {index.key: index for index in store.all()}
        rows = []
        for key in sorted(set(tracked) | set(stored)):
            index, entry = stored.get(key), tracked.get(key)
            info = index.info if index else {}
            rows.append({"key": key, "tracked": entry is not None, "source": entry.source if entry else None,
                         "project": info.get("project"), "index_directory": str(index.directory) if index else None,
                         "ready": info.get("ready", False), "indexed_at": info.get("indexed_at")})
        return {"registry": str(registry.path), "repos": rows}
    if args.command == "remove":
        with registry.edit() as entries:
            keys = [entry.resolved_key() for entry in entries]
            indexes = store.all()
            key = select(args.name, [*((key, entry.path) for key, entry in zip(keys, entries)),
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
        with registry.edit() as entries:
            keys = [entry.resolved_key() for entry in entries]
            for raw in args.sources:
                project = Path(raw).expanduser().resolve()
                if not project.is_dir():
                    raise Failure("project_missing", f"Project directory does not exist: {project}", 2)
                entry = Entry(str(project), project_key(project), Options(args.path, args.language, args.ignore, args.max_bytes))
                if entry.key in keys:
                    entries[keys.index(entry.key)] = entry
                else:
                    entries.append(entry)
                    keys.append(entry.key)
                added.append(entry)
        if args.no_sync:
            return {"registry": str(registry.path), "repos": [{"source": entry.source, "key": entry.key} for entry in added]}
        return sync(added, args.model, store, registry)
    return sync(registry.entries(), args.model, store, registry)


def sync(entries, model, store, registry):
    model_path, identity = check_runtime(model) if entries else (None, None)
    load = loader(model_path)
    results = []
    for entry in entries:
        try:
            project = entry.path
            if project is None or not project.is_dir():
                raise Failure("project_missing", f"Project directory does not exist: {entry.source}", 2)
            result = index_project(store, project, entry.resolved_key(), entry.options, model_path, identity, load)
            results.append({"source": entry.source, "ok": True, **result})
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
    return [
        {**dict(row), "score": max(-1.0, min(1.0, 1.0 - row["distance"] ** 2 / 2.0))} for row in rows
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
            if index is None or index.project is None:
                raise Failure("index_incompatible", "Index has no valid project identity", 4)
            with index.lock(False):
                info = index.manifest()
                if not info.get("ready"):
                    raise Failure("index_incomplete", "Last index did not complete; run index again", 4)
                if info.get("model_sha256") != identity:
                    raise Failure("model_mismatch", "Index uses different model assets; select a compatible project with --project", 4)
                results.extend({**row, "project": info["project"], "key": index.key} for row in search_rows(index, args, vector))
                projects.append(info["project"])
        except Failure as exc:
            raise Failure(exc.code, f"{directory}: {exc}", exc.exit_code) from exc
    results.sort(key=lambda row: (row["distance"], row["project"], row["path"], row["start_line"], row["end_line"], row["text"]))
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
