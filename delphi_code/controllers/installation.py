from ..infrastructure.store import Store
from ..services.progress import Progress
from ..ui.views.diagnostics import DiagnosticsView, ModelSetupView
from .response import ControllerResponse


def doctor(project: str, model: str) -> ControllerResponse:
    from ..services.diagnostics import diagnose

    diagnostics = diagnose(model, Store(), project)
    return ControllerResponse(diagnostics, [DiagnosticsView(diagnostics)])


def setup(model: str, source: str | None, progress: Progress) -> ControllerResponse:
    from ..services.model_installation import provision

    provisioned = provision(model, source, progress)
    view = ModelSetupView(
        provisioned["model"], provisioned["index_root"], provisioned["reused"], provisioned["diagnostics"]
    )
    return ControllerResponse(provisioned, [view])
