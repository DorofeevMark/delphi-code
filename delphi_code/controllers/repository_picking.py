from typing import Protocol

from ..domain.errors import ExitCode, Failure
from ..domain.selection import FileSelection
from ..domain.tracking_changes import TrackingChanges
from ..infrastructure.registry import Registry
from ..infrastructure.store import Store
from ..services.progress import Progress
from ..services.tracking import apply_tracking_changes


class Prompts(Protocol):
    def require_terminal(self) -> None: ...
    def choose_host(self, hosts): ...
    def choose_owner(self, host, owners) -> str | None: ...
    def type_owner(self, host) -> str | None: ...
    def choose_repositories(self, owner, repositories, tracked_keys) -> set[str] | None: ...
    def confirm_changes(self, changes: TrackingChanges) -> bool: ...


def pick_tracking_changes(hosts, tracked_keys, prompts: Prompts, progress: Progress) -> TrackingChanges | None:
    host = hosts[0] if len(hosts) == 1 else prompts.choose_host(hosts)
    if host is None:
        return None
    owner = _pick_owner(host, prompts, progress)
    if owner is None:
        return None
    progress.repositories_listing_started(host.name, owner)
    try:
        repositories = host.repositories(owner)
    finally:
        progress.listing_finished()
    if not repositories:
        raise Failure(
            "remote_empty", f"You have no repositories in the {host.name} {host.owner_noun} {owner}", ExitCode.USAGE
        )
    chosen_keys = prompts.choose_repositories(owner, repositories, tracked_keys)
    if chosen_keys is None:
        return None
    changes = TrackingChanges.from_selection(repositories, chosen_keys, tracked_keys)
    if changes and not prompts.confirm_changes(changes):
        return None
    return changes


def add_picked(
    ref: str | None, selection: FileSelection, model: str, sync_now: bool, progress: Progress, prompts: Prompts
) -> dict:
    from ..infrastructure.hosts import configured_hosts

    prompts.require_terminal()
    hosts = configured_hosts()
    if not hosts:
        raise Failure(
            "remote_auth_missing",
            "Picking repositories needs Bitbucket or GitHub credentials: set BITBUCKET_USERNAME and BITBUCKET_APP_PASSWORD or GITHUB_TOKEN, sign in with gh auth login, or store git credentials for bitbucket.org or github.com",
            ExitCode.RUNTIME_ASSETS,
        )
    store, registry = Store(), Registry()
    tracked = {entry.current_key() for entry in registry.entries()}
    changes = pick_tracking_changes(hosts, tracked, prompts, progress)
    if changes is None:
        return {"registry": str(registry.path), "cancelled": True, "repos": [], "removed": []}
    try:
        applied = apply_tracking_changes(store, registry, changes, ref, selection, model, sync_now, progress)
    except Failure as failure:
        failure.data = {**(failure.data or {}), "cancelled": False}
        raise
    return {**applied, "cancelled": False}


def _pick_owner(host, prompts: Prompts, progress: Progress) -> str | None:
    progress.owners_listing_started(host.name, host.owner_noun)
    try:
        owners = host.owners()
    except Failure as failure:
        if failure.code != "remote_permission_denied":
            raise
        owners = None
    finally:
        progress.listing_finished()
    if owners is None:
        return prompts.type_owner(host)
    if not owners:
        raise Failure("remote_empty", f"Your {host.name} account has no repositories", ExitCode.USAGE)
    return owners[0].slug if len(owners) == 1 else prompts.choose_owner(host, owners)
