from contextlib import redirect_stdout
import json
import os
import sys

from ..errors import Failure
from .text_layout import TextStyle, TextView

OUTPUT_SCHEMA_VERSION = 1


def stdout_reserved_for_result():
    return redirect_stdout(sys.stderr)


def json_output_requested(json_flag: bool) -> bool:
    return json_flag or not sys.stdout.isatty()


def write_json_success(command: str, data: dict) -> int:
    _write_json({"ok": True, "command": command, "data": data})
    return 0


def write_json_failure(command: str | None, failure: Failure) -> int:
    _write_failure_message(failure)
    payload = {"ok": False, "command": command, "error": failure.to_json()}
    if failure.data is not None:
        payload["data"] = failure.data
    _write_json(payload)
    return failure.exit_code


def write_text_success(views: list[TextView]) -> int:
    style = TextStyle.for_stdout()
    lines = [line for view in views for line in view.lines(style)]
    if lines:
        print("\n".join(lines))
    return 0


def write_text_failure(failure: Failure) -> int:
    _write_failure_message(failure)
    return failure.exit_code


def failure_of(exc: Exception) -> Failure:
    if os.environ.get("DELPHI_CODE_DEBUG") == "1":
        import traceback

        traceback.print_exc(file=sys.stderr)
    return Failure.from_exception(exc)


def _write_failure_message(failure: Failure):
    print(f"delphi-code: {failure}", file=sys.stderr)


def _write_json(payload: dict):
    print(
        json.dumps(
            {"schema_version": OUTPUT_SCHEMA_VERSION, **payload}, ensure_ascii=False, sort_keys=True, allow_nan=False
        )
    )
