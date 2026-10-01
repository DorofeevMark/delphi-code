from contextlib import redirect_stdout
import json
import os
import sys

from ..errors import Failure

OUTPUT_SCHEMA_VERSION = 1


def stdout_reserved_for_result():
    return redirect_stdout(sys.stderr)


def write_success(command: str, data: dict) -> int:
    _write_result({"ok": True, "command": command, "data": data})
    return 0


def write_failure(command: str | None, exc: Exception) -> int:
    if os.environ.get("DELPHI_CODE_DEBUG") == "1":
        import traceback

        traceback.print_exc(file=sys.stderr)
    failure = Failure.from_exception(exc)
    print(f"delphi-code: {failure}", file=sys.stderr)
    payload = {"ok": False, "command": command, "error": failure.to_json()}
    if failure.data is not None:
        payload["data"] = failure.data
    _write_result(payload)
    return failure.exit_code


def _write_result(payload: dict):
    print(
        json.dumps(
            {"schema_version": OUTPUT_SCHEMA_VERSION, **payload}, ensure_ascii=False, sort_keys=True, allow_nan=False
        )
    )
