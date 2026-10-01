from ..text_layout import LabelledFields, TextStyle, home_abbreviated, pluralized, relative_time
from .project_names import project_directory_unless_named_by_it, project_display_name


class IndexStateView:
    def __init__(self, state: dict):
        self._state = state

    def lines(self, style: TextStyle) -> list[str]:
        state = self._state
        readiness = style.green("ready") if state.get("ready") else style.yellow("incomplete; run index again")
        sizes = (
            f"{pluralized(state['files'], 'file')}, {pluralized(state['chunks'], 'chunk')}"
            if "files" in state
            else None
        )
        fields = LabelledFields(
            [
                ("State", readiness),
                ("Indexed", relative_time(state.get("indexed_at"))),
                ("Contents", sizes),
                ("Project", project_directory_unless_named_by_it(state["key"], state.get("project"))),
                ("Ref", state.get("ref")),
                ("Commit", state.get("commit")),
                ("Paths", ", ".join(state.get("paths") or [])),
                ("Languages", ", ".join(state.get("languages") or [])),
                ("Ignored", ", ".join(state.get("ignores") or [])),
                ("Model", home_abbreviated(state.get("model"))),
                ("Index", home_abbreviated(state.get("index_directory"))),
            ]
        )
        return [style.bold(project_display_name(state["key"])), *fields.lines(style)]
