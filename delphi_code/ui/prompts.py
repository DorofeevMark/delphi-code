import sys

from ..errors import ExitCode, Failure
from .repository_list import ask_repositories


class TerminalPrompts:
    def __init__(self, terminal=None):
        self._terminal = terminal

    def require_terminal(self):
        self._streams()

    def _streams(self):
        if self._terminal is not None:
            return self._terminal
        if not (sys.stdin.isatty() and sys.stderr.isatty()):
            raise Failure(
                "usage", "Pass SOURCE arguments, or run add in a terminal to pick repositories", ExitCode.USAGE
            )
        from prompt_toolkit.input import create_input
        from prompt_toolkit.output import create_output

        self._terminal = {"input": create_input(sys.stdin), "output": create_output(stdout=sys.stderr)}
        return self._terminal

    def choose_host(self, hosts):
        import questionary

        return questionary.select(
            "Hosting service",
            choices=[questionary.Choice(host.name, host) for host in hosts],
            **self._streams(),
        ).ask()

    def choose_owner(self, host, owners):
        import questionary

        return questionary.select(
            f"{host.name} {host.owner_noun}",
            choices=[questionary.Choice(_owner_title(owner), owner.slug) for owner in owners],
            use_search_filter=True,
            use_jk_keys=False,
            **self._streams(),
        ).ask()

    def type_owner(self, host):
        import questionary

        answer = questionary.text(
            f"These credentials cannot list {host.name} {host.owner_noun}s. {host.name} {host.owner_noun} to index:",
            default=host.suggested_owner() or "",
            **self._streams(),
        ).ask()
        return (answer or "").strip() or None

    def choose_repositories(self, owner, repositories, tracked_keys):
        return ask_repositories(f"Repositories to index in {owner}", repositories, tracked_keys, self._streams())

    def confirm_changes(self, changes):
        import questionary

        question = f"Track {len(changes.added)} and stop tracking {len(changes.removed)} repositories?"
        return questionary.confirm(question, default=True, **self._streams()).ask()


def _owner_title(owner):
    return owner.slug if owner.name == owner.slug else f"{owner.name} ({owner.slug})"
