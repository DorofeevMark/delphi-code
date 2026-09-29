from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from pathspec import GitIgnoreSpec

from .selection import FileSelection

ALWAYS_EXCLUDED_NAMES = {".git", ".delphi-code", ".venv", "venv", "node_modules", "__pycache__", ".models"}
IGNORE_FILE_NAMES = (".gitignore", ".delphi-codeignore")
FALLBACK_LANGUAGE = "text"

SourceFile = tuple[str, str, str]


class CollectedFiles(NamedTuple):
    files: dict[str, SourceFile]
    skipped: int


def collect(root: Path, selection: FileSelection, excluded: Path | None = None) -> CollectedFiles:
    collector = _Collector(root, selection, excluded)
    collector.walk(root, [])
    return CollectedFiles(collector.files, collector.skipped)


class _Collector:
    def __init__(self, root: Path, selection: FileSelection, excluded: Path | None):
        self.root = root
        self.selection = selection
        self.excluded = excluded
        self.extra_ignores = GitIgnoreSpec.from_lines(selection.ignores)
        self.files: dict[str, SourceFile] = {}
        self.skipped = 0

    def walk(self, directory: Path, inherited_rules: list[tuple[Path, GitIgnoreSpec]]):
        rules = inherited_rules + _ignore_rules_in(directory)
        for entry in sorted(directory.iterdir()):
            if self._is_skipped(entry, rules):
                self.skipped += 1
            elif entry.is_dir():
                self.walk(entry, rules)
            elif entry.is_file():
                self._read(entry)

    def _is_skipped(self, entry: Path, rules: list[tuple[Path, GitIgnoreSpec]]) -> bool:
        if entry.is_symlink() or entry.name in ALWAYS_EXCLUDED_NAMES or entry == self.excluded:
            return True
        suffix = "/" if entry.is_dir() else ""
        ignored = False
        for base, spec in rules:
            match = spec.check_file(entry.relative_to(base).as_posix() + suffix)
            if match.include is not None:
                ignored = match.include
        return ignored or self.extra_ignores.match_file(self._relative(entry) + suffix)

    def _read(self, entry: Path):
        from cocoindex.ops.text import detect_code_language

        relative = self._relative(entry)
        language = detect_code_language(filename=entry.name) or FALLBACK_LANGUAGE
        if not self.selection.includes(relative, language):
            return
        content = _text_content(entry, self.selection.max_bytes)
        if content is None:
            self.skipped += 1
        elif content.strip():
            self.files[relative] = (relative, language, content)

    def _relative(self, entry: Path) -> str:
        return entry.relative_to(self.root).as_posix()


def _ignore_rules_in(directory: Path) -> list[tuple[Path, GitIgnoreSpec]]:
    rules = []
    for name in IGNORE_FILE_NAMES:
        rule_file = directory / name
        if rule_file.is_file() and not rule_file.is_symlink():
            rules.append((directory, GitIgnoreSpec.from_lines(rule_file.read_text().splitlines())))
    return rules


def _text_content(path: Path, max_bytes: int) -> str | None:
    with path.open("rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes or b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None
