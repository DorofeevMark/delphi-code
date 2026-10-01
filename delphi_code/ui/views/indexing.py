from ..text_layout import LabelledFields, TextStyle, home_abbreviated, pluralized
from .project_names import project_directory_unless_named_by_it, project_display_name


class IndexingOutcomeView:
    def __init__(self, outcome: dict, shows_headline: bool):
        self._outcome = outcome
        self._shows_headline = shows_headline

    def lines(self, style: TextStyle) -> list[str]:
        outcome = self._outcome
        fields = LabelledFields(
            [
                ("Changes", file_change_summary(outcome.get("incremental", {})) or "none"),
                ("Project", project_directory_unless_named_by_it(outcome["key"], outcome.get("project"))),
                ("Ref", outcome.get("ref")),
                ("Commit", outcome.get("commit")),
                ("Skipped", pluralized(outcome["skipped"], "file") if outcome.get("skipped") else None),
                ("Index", home_abbreviated(outcome.get("index_directory"))),
            ]
        )
        headline = [indexing_outcome_line(outcome, style)] if self._shows_headline else []
        return headline + fields.lines(style)


class IndexingOutcomesSummaryView:
    def __init__(self, outcomes: list[dict], lists_each_outcome: bool):
        self._outcomes = outcomes
        self._lists_each_outcome = lists_each_outcome

    def lines(self, style: TextStyle) -> list[str]:
        listed = [self._outcome_line(outcome, style) for outcome in self._outcomes] if self._lists_each_outcome else []
        return [*listed, *([""] if listed else []), style.dim(self._totals_line())]

    def _outcome_line(self, outcome: dict, style: TextStyle) -> str:
        if outcome.get("ok"):
            return indexing_outcome_line(outcome, style)
        return f"{style.red('✗')} {style.bold(outcome['source'])}: {outcome['error']['message']}"

    def _totals_line(self) -> str:
        succeeded = [outcome for outcome in self._outcomes if outcome.get("ok")]
        unchanged_count = sum(bool(outcome.get("unchanged")) for outcome in succeeded)
        counts_by_result = [
            (len(succeeded) - unchanged_count, "indexed"),
            (unchanged_count, "up to date"),
            (len(self._outcomes) - len(succeeded), "failed"),
        ]
        totals = ", ".join(f"{count} {result}" for count, result in counts_by_result if count)
        return f"{pluralized(len(self._outcomes), 'project')}: {totals}"


def indexing_outcome_line(outcome: dict, style: TextStyle) -> str:
    name = style.bold(project_display_name(outcome["key"]))
    if outcome.get("unchanged"):
        return f"{style.green('✓')} {name} is up to date"
    sizes = f"{pluralized(outcome.get('files', 0), 'file')}, {pluralized(outcome.get('chunks', 0), 'chunk')}"
    changes = file_change_summary(outcome.get("incremental", {}))
    return f"{style.green('✓')} Indexed {name}: {sizes}" + (f" ({changes})" if changes else "")


def file_change_summary(incremental: dict) -> str:
    counts_by_change = [
        (incremental.get("num_adds", 0), "added"),
        (incremental.get("num_reprocesses", 0), "updated"),
        (incremental.get("num_deletes", 0), "removed"),
        (incremental.get("num_unchanged", 0), "unchanged"),
        (incremental.get("num_errors", 0), "failed"),
    ]
    return ", ".join(f"{count} {change}" for count, change in counts_by_change if count)
