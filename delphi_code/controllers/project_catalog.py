from ..infrastructure.registry import Registry
from ..infrastructure.store import Store
from ..services import project_catalog, tracking
from ..ui.text_layout import Message
from ..ui.views.index_state import IndexStateView
from ..ui.views.project_table import ProjectTableView
from ..ui.views.tracking import TRACK_A_PROJECT_HINT, UntrackedProjectView
from .response import ControllerResponse


def status(project: str) -> ControllerResponse:
    state = project_catalog.status(Store(), project)
    return ControllerResponse(state, [IndexStateView(state)])


def list_tracked() -> ControllerResponse:
    listing = project_catalog.list_projects(Store(), Registry())
    if not listing["repos"]:
        return ControllerResponse(listing, [Message("No projects are tracked or indexed yet.", TRACK_A_PROJECT_HINT)])
    return ControllerResponse(listing, [ProjectTableView(listing["repos"])])


def remove(name: str, keep_index: bool) -> ControllerResponse:
    removal = tracking.remove_project(Store(), Registry(), name, keep_index)
    view = UntrackedProjectView(removal["key"], removal["untracked"], removal["deleted_index"])
    return ControllerResponse(removal, [view])
