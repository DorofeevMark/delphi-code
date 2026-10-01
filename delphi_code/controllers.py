from typing import NamedTuple, Protocol

from . import projects
from .errors import ExitCode, Failure
from .progress import Progress
from .projects import SearchRequest, TrackingChanges
from .registry import Registry
from .selection import FileSelection
from .store import Store
from .ui.text_layout import Message, TextView, pluralized
from .ui.views.diagnostics import DiagnosticsView, ModelSetupView
from .ui.views.index_state import IndexStateView
from .ui.views.indexing import IndexingOutcomesSummaryView, IndexingOutcomeView
from .ui.views.project_names import project_display_name
from .ui.views.project_table import ProjectTableView
from .ui.views.search_hits import SearchHitsView
from .ui.views.tracking import NewlyTrackedProjectsView, UntrackedProjectView

TRACK_A_PROJECT_HINT = "Track one with: delphi-code add PATH_OR_REPOSITORY"


class Prompts(Protocol):
    def require_terminal(self) -> None: ...
    def choose_host(self, hosts): ...
    def choose_owner(self, host, owners) -> str | None: ...
    def type_owner(self, host) -> str | None: ...
    def choose_repositories(self, owner, repositories, tracked_keys) -> set[str] | None: ...
    def confirm_changes(self, changes: TrackingChanges) -> bool: ...


class ControllerResponse(NamedTuple):
    data: dict
    views: list[TextView]


def index(project: str, selection: FileSelection, model: str, progress: Progress) -> ControllerResponse:
    outcome = projects.index_local_project(Store(), project, selection, model, progress)
    return ControllerResponse(outcome, [IndexingOutcomeView(outcome, shows_headline=not progress.showed_outcome_lines)])


def search(project: str | None, request: SearchRequest, model: str) -> ControllerResponse:
    if project is None:
        found = projects.search_everywhere(Store(), request, model)
        searched_scope = pluralized(len(found["projects"]), "index", "indexes")
    else:
        found = projects.search_project(Store(), project, request, model)
        searched_scope = project_display_name(found["key"])
    view = SearchHitsView(request.query, searched_scope, found["results"], names_project_of_each_hit=project is None)
    return ControllerResponse(found, [view])


def status(project: str) -> ControllerResponse:
    state = projects.status(Store(), project)
    return ControllerResponse(state, [IndexStateView(state)])


def doctor(project: str, model: str) -> ControllerResponse:
    from .doctor import diagnose

    diagnostics = diagnose(model, Store(), project)
    return ControllerResponse(diagnostics, [DiagnosticsView(diagnostics)])


def add(
    sources: list[str],
    ref: str | None,
    selection: FileSelection,
    model: str,
    sync_now: bool,
    progress: Progress,
    prompts: Prompts,
) -> ControllerResponse:
    if sources:
        added = projects.add_sources(Store(), Registry(), sources, ref, selection, model, sync_now, progress)
    else:
        added = _add_picked(ref, selection, model, sync_now, progress, prompts)
    return ControllerResponse(added, _tracking_change_views(added, sync_now, progress))


def sync(model: str, progress: Progress) -> ControllerResponse:
    registry = Registry()
    synced = projects.sync(Store(), registry, registry.entries(), model, progress)
    if not synced["repos"]:
        return ControllerResponse(synced, [Message("No projects are tracked.", TRACK_A_PROJECT_HINT)])
    return ControllerResponse(synced, [_indexing_outcomes_view(synced["repos"], progress)])


def list_tracked() -> ControllerResponse:
    listing = projects.list_projects(Store(), Registry())
    if not listing["repos"]:
        return ControllerResponse(listing, [Message("No projects are tracked or indexed yet.", TRACK_A_PROJECT_HINT)])
    return ControllerResponse(listing, [ProjectTableView(listing["repos"])])


def remove(name: str, keep_index: bool) -> ControllerResponse:
    removal = projects.remove_project(Store(), Registry(), name, keep_index)
    view = UntrackedProjectView(removal["key"], removal["untracked"], removal["deleted_index"])
    return ControllerResponse(removal, [view])


def setup(model: str, source: str | None, progress: Progress) -> ControllerResponse:
    from .setup import provision

    provisioned = provision(model, source, progress)
    view = ModelSetupView(
        provisioned["model"], provisioned["index_root"], provisioned["reused"], provisioned["diagnostics"]
    )
    return ControllerResponse(provisioned, [view])


def _tracking_change_views(added: dict, sync_now: bool, progress: Progress) -> list[TextView]:
    if added.get("cancelled"):
        return [Message("Cancelled; nothing changed")]
    removed = added.get("removed", [])
    if not added["repos"] and not removed:
        return [Message("Nothing changed")]
    views: list[TextView] = [
        _indexing_outcomes_view(added["repos"], progress)
        if sync_now and added["repos"]
        else NewlyTrackedProjectsView([repository["key"] for repository in added["repos"]])
    ]
    views.extend(UntrackedProjectView(project["key"], True, project["deleted_index"]) for project in removed)
    return views


def _indexing_outcomes_view(outcomes: list[dict], progress: Progress) -> TextView:
    return IndexingOutcomesSummaryView(outcomes, lists_each_outcome=not progress.showed_outcome_lines)


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


def _add_picked(
    ref: str | None, selection: FileSelection, model: str, sync_now: bool, progress: Progress, prompts: Prompts
) -> dict:
    from .hosts import configured_hosts

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
        applied = projects.apply_tracking_changes(store, registry, changes, ref, selection, model, sync_now, progress)
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
