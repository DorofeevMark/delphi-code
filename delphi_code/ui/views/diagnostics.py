from ..text_layout import LabelledFields, TextStyle, home_abbreviated
from .project_names import project_display_name


class DiagnosticsView:
    def __init__(self, diagnostics: dict):
        self._diagnostics = diagnostics

    def lines(self, style: TextStyle) -> list[str]:
        return [
            f"{style.green('✓')} Installation works",
            *LabelledFields(diagnostic_fields(self._diagnostics)).lines(style),
        ]


class ModelSetupView:
    def __init__(self, model_directory: str, index_root: str, reused_existing_model: bool, diagnostics: dict):
        self._model_directory = model_directory
        self._index_root = index_root
        self._reused_existing_model = reused_existing_model
        self._diagnostics = diagnostics

    def lines(self, style: TextStyle) -> list[str]:
        outcome = "Model already installed" if self._reused_existing_model else "Model installed"
        fields = LabelledFields(
            [("Indexes", home_abbreviated(self._index_root)), *diagnostic_fields(self._diagnostics)]
        )
        return [f"{style.green('✓')} {outcome} at {home_abbreviated(self._model_directory)}", *fields.lines(style)]


def diagnostic_fields(diagnostics: dict) -> list[tuple[str, str | None]]:
    dependencies = diagnostics.get("dependencies", {})
    return [
        ("Project", project_display_name(diagnostics["key"]) if diagnostics.get("key") else None),
        ("Index", home_abbreviated(diagnostics.get("index_directory")) or "none yet"),
        ("Model", home_abbreviated(diagnostics.get("model"))),
        ("Embedding", f"{diagnostics['dimensions']} dimensions on {diagnostics['device']}"),
        ("Storage", diagnostics.get("cocoindex_storage")),
        ("Network guard", diagnostics.get("network_guard")),
        ("SQLite", diagnostics.get("sqlite")),
        ("Packages", ", ".join(f"{name} {version}" for name, version in dependencies.items())),
    ]
