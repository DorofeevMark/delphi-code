from ..text_layout import TextStyle
from .project_names import project_display_name


class UntrackedProjectView:
    def __init__(self, key: str, was_tracked: bool, deleted_index_directory: str | None):
        self._key = key
        self._was_tracked = was_tracked
        self._deleted_index_directory = deleted_index_directory

    def lines(self, style: TextStyle) -> list[str]:
        name = style.bold(project_display_name(self._key))
        undone_actions = []
        if self._was_tracked:
            undone_actions.append("stopped tracking it")
        if self._deleted_index_directory:
            undone_actions.append("deleted its index")
        if not undone_actions:
            return [f"{name} was not tracked; its index was kept"]
        return [f"{style.green('✓')} {name}: {' and '.join(undone_actions)}"]


class NewlyTrackedProjectsView:
    def __init__(self, keys: list[str]):
        self._keys = keys

    def lines(self, style: TextStyle) -> list[str]:
        if not self._keys:
            return []
        tracked = [f"{style.green('✓')} Tracking {style.bold(project_display_name(key))}" for key in self._keys]
        return [*tracked, style.dim("Index them with: delphi-code sync")]
