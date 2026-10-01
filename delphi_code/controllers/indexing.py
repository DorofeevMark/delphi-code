from ..domain.selection import FileSelection
from ..infrastructure.registry import Registry
from ..infrastructure.store import Store
from ..services import indexing, tracking
from ..services.progress import Progress
from ..ui.text_layout import Message, TextView
from ..ui.views.indexing import IndexingOutcomesSummaryView, IndexingOutcomeView
from ..ui.views.tracking import TRACK_A_PROJECT_HINT, NewlyTrackedProjectsView, UntrackedProjectView
from . import repository_picking
from .repository_picking import Prompts
from .response import ControllerResponse


def index(project: str, selection: FileSelection, model: str, progress: Progress) -> ControllerResponse:
    outcome = indexing.index_local_project(Store(), project, selection, model, progress)
    return ControllerResponse(outcome, [IndexingOutcomeView(outcome, shows_headline=not progress.showed_outcome_lines)])


def sync(model: str, progress: Progress) -> ControllerResponse:
    registry = Registry()
    synced = indexing.sync(Store(), registry, registry.entries(), model, progress)
    if not synced["repos"]:
        return ControllerResponse(synced, [Message("No projects are tracked.", TRACK_A_PROJECT_HINT)])
    return ControllerResponse(synced, [_indexing_outcomes_view(synced["repos"], progress)])


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
        added = tracking.add_sources(Store(), Registry(), sources, ref, selection, model, sync_now, progress)
    else:
        added = repository_picking.add_picked(ref, selection, model, sync_now, progress, prompts)
    return ControllerResponse(added, _tracking_change_views(added, sync_now, progress))


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
