import sys
from typing import NamedTuple

from .model import Failure

DESCRIPTION_WIDTH = 60


class TrackingChanges(NamedTuple):
    added: list[str]
    removed: list[str]

    @classmethod
    def from_selection(cls, repositories, chosen_keys, tracked_keys):
        return cls(
            added=[repository.key for repository in repositories if repository.key in chosen_keys and repository.key not in tracked_keys],
            removed=[repository.key for repository in repositories if repository.key not in chosen_keys and repository.key in tracked_keys],
        )

    def __bool__(self):
        return bool(self.added or self.removed)


class RepositoryPicker:
    def __init__(self, catalog=None, terminal=None):
        self._terminal = _stderr_terminal() if terminal is None else terminal
        if catalog is None:
            from .bitbucket_catalog import BitbucketCatalog

            catalog = BitbucketCatalog()
        self._catalog = catalog

    def choose(self, tracked_keys):
        workspace = self._choose_workspace()
        if workspace is None:
            return None
        repositories = self._catalog.repositories(workspace)
        if not repositories:
            raise Failure("remote_empty", f"You have no repositories in the Bitbucket workspace {workspace}", 2)
        chosen_keys = self._choose_repositories(workspace, repositories, tracked_keys)
        if chosen_keys is None:
            return None
        changes = TrackingChanges.from_selection(repositories, chosen_keys, tracked_keys)
        if changes and not self._confirm(changes):
            return None
        return changes

    def _choose_workspace(self):
        import questionary

        workspaces = self._catalog.workspaces()
        if not workspaces:
            raise Failure("remote_empty", "Your Bitbucket account has no workspaces", 2)
        if len(workspaces) == 1:
            return workspaces[0].slug
        return questionary.select(
            "Bitbucket workspace",
            choices=[questionary.Choice(f"{workspace.name} ({workspace.slug})", workspace.slug) for workspace in workspaces],
            use_search_filter=True, use_jk_keys=False, **self._terminal,
        ).ask()

    def _choose_repositories(self, workspace, repositories, tracked_keys):
        import questionary

        chosen = questionary.checkbox(
            f"Repositories to index in {workspace} (space toggles, type to filter)",
            choices=[questionary.Choice(_title(repository), repository.key, checked=repository.key in tracked_keys) for repository in repositories],
            use_search_filter=True, use_jk_keys=False, **self._terminal,
        ).ask()
        return None if chosen is None else set(chosen)

    def _confirm(self, changes):
        import questionary

        question = f"Track {len(changes.added)} and stop tracking {len(changes.removed)} repositories?"
        return questionary.confirm(question, default=True, **self._terminal).ask()


def _title(repository):
    description = repository.description.splitlines()[0] if repository.description else ""
    if len(description) > DESCRIPTION_WIDTH:
        description = description[:DESCRIPTION_WIDTH - 1] + "…"
    return f"{repository.slug}{' (private)' if repository.private else ''}{'  ' + description if description else ''}"


def _stderr_terminal():
    if not (sys.stdin.isatty() and sys.stderr.isatty()):
        raise Failure("usage", "Pass SOURCE arguments, or run add in a terminal to pick Bitbucket repositories", 2)
    from prompt_toolkit.input import create_input
    from prompt_toolkit.output import create_output

    return {"input": create_input(sys.stdin), "output": create_output(stdout=sys.stderr)}
