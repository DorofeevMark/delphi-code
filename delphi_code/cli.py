import argparse
from collections.abc import Callable
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import sys

from . import projects
from .errors import ExitCode, Failure
from .paths import model_directory
from .registry import Registry
from .selection import DEFAULT_MAX_BYTES, FileSelection
from .store import Store

OUTPUT_SCHEMA_VERSION = 1
MAX_SEARCH_LIMIT = 1000


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Failure("usage", message, ExitCode.USAGE)


def arguments() -> argparse.Namespace:
    parser = Parser(prog="delphi-code", description="Offline local code search")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("index", "search", "status", "doctor"):
        command = commands.add_parser(name)
        command.add_argument(
            "--project",
            "-p",
            default=None if name == "search" else str(Path.cwd()),
            help="Project path, key, or indexed name; search defaults to all indexes",
        )
        if name != "status":
            _add_model_option(command)
        if name in {"index", "search"}:
            _add_selection_options(command, indexing=name == "index")
        if name == "search":
            command.add_argument("query")
            command.add_argument("--limit", type=_search_limit, default=10)
    add = commands.add_parser("add", help="Track projects in the registry and index them")
    add.add_argument(
        "sources",
        nargs="*",
        metavar="SOURCE",
        help="Project directory, bitbucket.org/workspace/repository, or github.com/owner/repository; "
        "omit to pick repositories interactively",
    )
    add.add_argument("--ref", help="Branch or tag of remote repositories; defaults to the default branch")
    _add_selection_options(add, indexing=True)
    _add_model_option(add)
    add.add_argument("--no-sync", action="store_true", help="Only update the registry")
    sync = commands.add_parser("sync", help="Index every tracked project")
    _add_model_option(sync)
    commands.add_parser("list", help="List tracked projects and stored indexes")
    remove = commands.add_parser("remove", help="Stop tracking a project and delete its index")
    remove.add_argument("name", help="Project path, key, or indexed name")
    remove.add_argument("--keep-index", action="store_true", help="Only untrack the project")
    setup = commands.add_parser("setup", help="Download or import the pinned model and check the installation")
    setup.add_argument("--from", dest="source", help="Import a prepared MiniLM model without network access")
    _add_model_option(setup, help="Model destination")
    return parser.parse_args()


def execute(args: argparse.Namespace) -> dict:
    return COMMANDS[args.command](args)


def main():
    command = None
    exit_code = 0
    try:
        args = arguments()
        command = args.command
        with redirect_stdout(sys.stderr):
            payload = {"ok": True, "command": command, "data": execute(args)}
    except Exception as exc:
        if os.environ.get("DELPHI_CODE_DEBUG") == "1":
            import traceback

            traceback.print_exc(file=sys.stderr)
        failure = Failure.from_exception(exc)
        exit_code = failure.exit_code
        print(f"delphi-code: {failure}", file=sys.stderr)
        payload = {"ok": False, "command": command, "error": failure.to_json()}
        if failure.data is not None:
            payload["data"] = failure.data
    print(
        json.dumps(
            {"schema_version": OUTPUT_SCHEMA_VERSION, **payload}, ensure_ascii=False, sort_keys=True, allow_nan=False
        )
    )
    raise SystemExit(exit_code)


def _index(args):
    return projects.index_local_project(Store(), args.project, _selection(args), args.model)


def _search(args):
    request = projects.SearchRequest(args.query, args.path, args.language, args.limit)
    if args.project is None:
        return projects.search_everywhere(Store(), request, args.model)
    return projects.search_project(Store(), args.project, request, args.model)


def _status(args):
    return projects.status(Store(), args.project)


def _doctor(args):
    from .doctor import diagnose

    return diagnose(args.model, Store(), args.project)


def _add(args):
    if not args.sources:
        return projects.add_picked_repositories(
            Store(), Registry(), args.ref, _selection(args), args.model, sync_now=not args.no_sync
        )
    return projects.add_sources(
        Store(), Registry(), args.sources, args.ref, _selection(args), args.model, sync_now=not args.no_sync
    )


def _sync(args):
    registry = Registry()
    return projects.sync(Store(), registry, registry.entries(), args.model)


def _list(args):
    return projects.list_projects(Store(), Registry())


def _remove(args):
    return projects.remove_project(Store(), Registry(), args.name, keep_index=args.keep_index)


def _setup(args):
    from .setup import provision

    return provision(args.model, args.source)


COMMANDS: dict[str, Callable[[argparse.Namespace], dict]] = {
    "index": _index,
    "search": _search,
    "status": _status,
    "doctor": _doctor,
    "add": _add,
    "sync": _sync,
    "list": _list,
    "remove": _remove,
    "setup": _setup,
}


def _selection(args) -> FileSelection:
    return FileSelection(args.path, args.language, args.ignore, args.max_bytes)


def _add_selection_options(command, indexing):
    command.add_argument("--path", action="append", default=[], help="Project-relative glob; repeat for alternatives")
    command.add_argument("--language", action="append", default=[])
    if indexing:
        command.add_argument("--ignore", action="append", default=[])
        command.add_argument("--max-bytes", type=_positive_integer, default=DEFAULT_MAX_BYTES)


def _add_model_option(command, help=None):
    command.add_argument("--model", default=os.environ.get("DELPHI_CODE_MODEL") or model_directory(), help=help)


def _positive_integer(text):
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def _search_limit(text):
    value = int(text)
    if not 1 <= value <= MAX_SEARCH_LIMIT:
        raise argparse.ArgumentTypeError(f"must be between 1 and {MAX_SEARCH_LIMIT}")
    return value
