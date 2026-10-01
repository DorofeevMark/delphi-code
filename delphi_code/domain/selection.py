from dataclasses import dataclass, field
import fnmatch

DEFAULT_MAX_BYTES = 1_048_576


@dataclass
class FileSelection:
    paths: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    ignores: list[str] = field(default_factory=list)
    max_bytes: int = DEFAULT_MAX_BYTES

    def includes(self, path: str, language: str) -> bool:
        path_selected = not self.paths or any(fnmatch.fnmatchcase(path, pattern) for pattern in self.paths)
        language_selected = not self.languages or language in self.languages
        return path_selected and language_selected
