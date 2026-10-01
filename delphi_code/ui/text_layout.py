from __future__ import annotations

from datetime import UTC, datetime
import os
from pathlib import Path
import sys
import textwrap
from typing import Protocol

SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 60 * SECONDS_PER_MINUTE
SECONDS_PER_DAY = 24 * SECONDS_PER_HOUR
DAYS_SHOWN_AS_RELATIVE = 30


class TextStyle:
    def __init__(self, colors_enabled: bool):
        self.colors_enabled = colors_enabled

    @classmethod
    def for_stdout(cls) -> TextStyle:
        return cls(sys.stdout.isatty() and "NO_COLOR" not in os.environ)

    def bold(self, text: str) -> str:
        return self._colored("1", text)

    def dim(self, text: str) -> str:
        return self._colored("2", text)

    def green(self, text: str) -> str:
        return self._colored("32", text)

    def yellow(self, text: str) -> str:
        return self._colored("33", text)

    def red(self, text: str) -> str:
        return self._colored("31", text)

    def cyan(self, text: str) -> str:
        return self._colored("36", text)

    def _colored(self, ansi_code: str, text: str) -> str:
        return f"\033[{ansi_code}m{text}\033[0m" if self.colors_enabled and text else text


class TextView(Protocol):
    def lines(self, style: TextStyle) -> list[str]: ...


class Message:
    def __init__(self, text: str, hint: str | None = None):
        self._text = text
        self._hint = hint

    def lines(self, style: TextStyle) -> list[str]:
        return [self._text, *([style.dim(self._hint)] if self._hint else [])]


class Table:
    def __init__(self, *header: str):
        self._rows = [header]

    def add_row(self, *cells: str):
        self._rows.append(cells)

    def lines(self, style: TextStyle) -> list[str]:
        column_widths = [max(len(row[column]) for row in self._rows) for column in range(len(self._rows[0]))]
        aligned_rows = [
            "  ".join(cell.ljust(width) for cell, width in zip(row, column_widths, strict=True)).rstrip()
            for row in self._rows
        ]
        header, *body = aligned_rows
        return [style.dim(header), *body]


class LabelledFields:
    def __init__(self, fields: list[tuple[str, str | None]]):
        self._present_fields = [(label, value) for label, value in fields if value]

    def lines(self, style: TextStyle) -> list[str]:
        label_width = max((len(label) for label, _ in self._present_fields), default=0)
        return [f"  {style.dim(label.ljust(label_width))}  {value}" for label, value in self._present_fields]


def excerpt_lines(text: str, line_limit: int, indent: str, style: TextStyle) -> list[str]:
    source_lines = textwrap.dedent(text.strip("\n")).splitlines()
    shown_lines = [f"{indent}{line}".rstrip() for line in source_lines[:line_limit]]
    hidden_line_count = len(source_lines) - len(shown_lines)
    if hidden_line_count > 0:
        shown_lines.append(style.dim(f"{indent}… {pluralized(hidden_line_count, 'more line')}"))
    return shown_lines


def pluralized(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else plural or singular + 's'}"


def home_abbreviated(path: str | None) -> str | None:
    if not path:
        return None
    home = str(Path.home())
    is_inside_home = path == home or path.startswith(home + os.sep)
    return "~" + path[len(home) :] if is_inside_home else path


def relative_time(iso_timestamp: str | None) -> str | None:
    if not iso_timestamp:
        return None
    try:
        moment = datetime.fromisoformat(iso_timestamp)
        elapsed_seconds = (datetime.now(UTC) - moment).total_seconds()
    except (ValueError, TypeError):
        return iso_timestamp
    if elapsed_seconds < SECONDS_PER_MINUTE:
        return "just now"
    if elapsed_seconds < SECONDS_PER_HOUR:
        return f"{pluralized(int(elapsed_seconds // SECONDS_PER_MINUTE), 'minute')} ago"
    if elapsed_seconds < SECONDS_PER_DAY:
        return f"{pluralized(int(elapsed_seconds // SECONDS_PER_HOUR), 'hour')} ago"
    if elapsed_seconds < DAYS_SHOWN_AS_RELATIVE * SECONDS_PER_DAY:
        return f"{pluralized(int(elapsed_seconds // SECONDS_PER_DAY), 'day')} ago"
    return moment.astimezone().strftime("%Y-%m-%d")
