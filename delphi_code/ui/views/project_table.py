from ..text_layout import Table, TextStyle, pluralized, relative_time
from .project_names import project_display_name

SHORT_COMMIT_LENGTH = 12
TRACKED_MARK = "●"
ONLY_INDEXED_MARK = "○"


class ProjectTableView:
    def __init__(self, projects: list[dict]):
        self._projects = projects

    def lines(self, style: TextStyle) -> list[str]:
        table = Table("", "PROJECT", "STATE", "INDEXED", "REVISION")
        for project in self._projects:
            table.add_row(
                TRACKED_MARK if project["tracked"] else ONLY_INDEXED_MARK,
                project_display_name(project["key"]),
                _index_state_label(project),
                relative_time(project.get("indexed_at")) or "",
                _short_revision_label(project),
            )
        return [*table.lines(style), "", style.dim(self._legend())]

    def _legend(self) -> str:
        tracked_count = sum(project["tracked"] for project in self._projects)
        only_indexed_count = len(self._projects) - tracked_count
        return (
            f"{pluralized(tracked_count, 'tracked project')} ({TRACKED_MARK}), "
            f"{only_indexed_count} only indexed ({ONLY_INDEXED_MARK})"
        )


def _index_state_label(project: dict) -> str:
    if project.get("ready"):
        return "ready"
    return "incomplete" if project.get("index_directory") else "not indexed"


def _short_revision_label(project: dict) -> str:
    commit = project.get("commit")
    if not commit:
        return ""
    short_commit = commit[:SHORT_COMMIT_LENGTH]
    return f"{project['ref']}@{short_commit}" if project.get("ref") else short_commit
