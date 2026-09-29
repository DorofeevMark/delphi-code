import sys
from typing import NamedTuple

from .errors import ExitCode, Failure

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
    def __init__(self, hosts=None, terminal=None):
        self._terminal = _stderr_terminal() if terminal is None else terminal
        if hosts is None:
            from .hosts import configured_hosts

            hosts = configured_hosts()
        if not hosts:
            raise Failure("remote_auth_missing", "Picking repositories needs Bitbucket or GitHub credentials: set BITBUCKET_USERNAME and BITBUCKET_APP_PASSWORD or GITHUB_TOKEN, sign in with gh auth login, or store git credentials for bitbucket.org or github.com", ExitCode.RUNTIME_ASSETS)
        self._hosts = hosts

    def choose(self, tracked_keys):
        host = self._choose_host()
        if host is None:
            return None
        owner = self._choose_owner(host)
        if owner is None:
            return None
        repositories = host.repositories(owner)
        if not repositories:
            raise Failure("remote_empty", f"You have no repositories in the {host.name} {host.owner_noun} {owner}", ExitCode.USAGE)
        chosen_keys = self._choose_repositories(owner, repositories, tracked_keys)
        if chosen_keys is None:
            return None
        changes = TrackingChanges.from_selection(repositories, chosen_keys, tracked_keys)
        if changes and not self._confirm(changes):
            return None
        return changes

    def _choose_host(self):
        import questionary

        if len(self._hosts) == 1:
            return self._hosts[0]
        return questionary.select(
            "Hosting service",
            choices=[questionary.Choice(host.name, host) for host in self._hosts], **self._terminal,
        ).ask()

    def _choose_owner(self, host):
        import questionary

        owners = host.owners()
        if not owners:
            raise Failure("remote_empty", f"Your {host.name} account has no repositories", ExitCode.USAGE)
        if len(owners) == 1:
            return owners[0].slug
        return questionary.select(
            f"{host.name} {host.owner_noun}",
            choices=[questionary.Choice(_owner_title(owner), owner.slug) for owner in owners],
            use_search_filter=True, use_jk_keys=False, **self._terminal,
        ).ask()

    def _choose_repositories(self, owner, repositories, tracked_keys):
        import questionary

        chosen = questionary.checkbox(
            f"Repositories to index in {owner} (space toggles, type to filter)",
            choices=[questionary.Choice(_repository_title(repository), repository.key, checked=repository.key in tracked_keys) for repository in repositories],
            use_search_filter=True, use_jk_keys=False, **self._terminal,
        ).ask()
        return None if chosen is None else set(chosen)

    def _confirm(self, changes):
        import questionary

        question = f"Track {len(changes.added)} and stop tracking {len(changes.removed)} repositories?"
        return questionary.confirm(question, default=True, **self._terminal).ask()


def _owner_title(owner):
    return owner.slug if owner.name == owner.slug else f"{owner.name} ({owner.slug})"


def _repository_title(repository):
    description = repository.description.splitlines()[0] if repository.description else ""
    if len(description) > DESCRIPTION_WIDTH:
        description = description[:DESCRIPTION_WIDTH - 1] + "…"
    return f"{repository.slug}{' (private)' if repository.private else ''}{'  ' + description if description else ''}"


def _stderr_terminal():
    if not (sys.stdin.isatty() and sys.stderr.isatty()):
        raise Failure("usage", "Pass SOURCE arguments, or run add in a terminal to pick repositories", ExitCode.USAGE)
    from prompt_toolkit.input import create_input
    from prompt_toolkit.output import create_output

    return {"input": create_input(sys.stdin), "output": create_output(stdout=sys.stderr)}
