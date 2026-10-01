import argparse
from collections.abc import Callable
import os
from pathlib import Path
import sys

from .controllers import indexing, installation, project_catalog, searching
from .controllers.response import ControllerResponse
from .domain.errors import ExitCode, Failure
from .domain.selection import DEFAULT_MAX_BYTES, FileSelection
from .infrastructure.paths import model_directory
from .services.progress import Progress
from .services.searching import SearchRequest
from .ui import output
from .ui.prompts import TerminalPrompts
from .ui.terminal import TerminalProgress

MAX_SEARCH_LIMIT = 1000


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Failure("usage", message, ExitCode.USAGE)


def arguments() -> argparse.Namespace:
    parser = Parser(prog="delphi-code", description="Offline local code search")
    output_format_options = Parser(add_help=False)
    output_format_options.add_argument(
        "--json", action="store_true", help="Write JSON even on a terminal; JSON is the default when piped"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def add_command_parser(name, **options):
        return commands.add_parser(name, parents=[output_format_options], **options)

    for name, summary in (
        ("index", "Build or update a project's index"),
        ("search", "Search indexed code with a natural-language query"),
        ("status", "Show a project's stored index state"),
        ("doctor", "Check the model, SQLite extensions and native storage"),
    ):
        command = add_command_parser(name, help=summary)
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
    add = add_command_parser("add", help="Track projects in the registry and index them")
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
    sync = add_command_parser("sync", help="Index every tracked project")
    _add_model_option(sync)
    listing = add_command_parser("list", help="List tracked projects and stored indexes")
    listing.add_argument("--indexed", action="store_true", help="Only list projects that have an index")
    remove = add_command_parser("remove", help="Stop tracking a project and delete its index")
    remove.add_argument("name", help="Project path, key, or indexed name")
    remove.add_argument("--keep-index", action="store_true", help="Only untrack the project")
    setup = add_command_parser("setup", help="Download or import the pinned model and check the installation")
    setup.add_argument("--from", dest="source", help="Import a prepared MiniLM model without network access")
    _add_model_option(setup, help="Model destination")
    return parser.parse_args()


def execute(args: argparse.Namespace, progress: TerminalProgress | None = None) -> ControllerResponse:
    progress = progress or TerminalProgress.on_terminal()
    with progress:
        return COMMANDS[args.command](args, progress)


def main():
    command, write_json = None, output.json_output_requested(json_flag="--json" in sys.argv[1:])
    progress = TerminalProgress.on_terminal()
    try:
        args = arguments()
        command, write_json = args.command, output.json_output_requested(json_flag=args.json)
        with output.stdout_reserved_for_result():
            response = execute(args, progress)
    except Exception as exc:
        failure = output.failure_of(exc)
        if write_json:
            raise SystemExit(output.write_json_failure(command, failure)) from None
        raise SystemExit(output.write_text_failure(failure)) from None
    if write_json:
        raise SystemExit(output.write_json_success(command, response.data))
    raise SystemExit(output.write_text_success(response.views))


COMMANDS: dict[str, Callable[[argparse.Namespace, Progress], ControllerResponse]] = {
    "index": lambda args, progress: indexing.index(args.project, _selection(args), args.model, progress),
    "search": lambda args, progress: searching.search(
        args.project, SearchRequest(args.query, args.path, args.language, args.limit), args.model
    ),
    "status": lambda args, progress: project_catalog.status(args.project),
    "doctor": lambda args, progress: installation.doctor(args.project, args.model),
    "add": lambda args, progress: indexing.add(
        args.sources, args.ref, _selection(args), args.model, not args.no_sync, progress, TerminalPrompts()
    ),
    "sync": lambda args, progress: indexing.sync(args.model, progress),
    "list": lambda args, progress: project_catalog.list_tracked(args.indexed),
    "remove": lambda args, progress: project_catalog.remove(args.name, args.keep_index),
    "setup": lambda args, progress: installation.setup(args.model, args.source, progress),
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
