from pathlib import Path

from ..infrastructure.model import LocalModel
from ..infrastructure.store import require_sqlite_extensions


def open_model(location: str | Path | None) -> LocalModel:
    model = LocalModel.inspect(location)
    require_sqlite_extensions()
    return model
