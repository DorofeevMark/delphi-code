import asyncio
from importlib.metadata import version
from pathlib import Path
import sqlite3
import tempfile

from ..infrastructure.store import Store, vector_database
from .local_model import open_model
from .project_catalog import resolve_existing

REPORTED_DEPENDENCIES = ("cocoindex", "sqlite-vec", "sentence-transformers", "torch")
PROBE_TEXT = "local code search"


def diagnose(model_location: str, store: Store, project_name: str) -> dict:
    from ..infrastructure.vector_index import check_storage

    project, key, index = resolve_existing(store, project_name)
    model = open_model(model_location)
    with tempfile.TemporaryDirectory(prefix="delphi-code-doctor-") as scratch:
        asyncio.run(check_storage(Path(scratch)))
    vector = model.embed([PROBE_TEXT])[0]
    with vector_database(":memory:") as db:
        self_distance = db.execute("SELECT vec_distance_L2(?, ?)", (vector.tobytes(), vector.tobytes())).fetchone()[0]
    return {
        "version": version("delphi-code"),
        "project": str(project) if project else None,
        "key": key,
        "index_directory": str(index.directory) if index else None,
        "model": str(model.directory),
        "model_sha256": model.sha256,
        "dimensions": len(vector),
        "self_distance": self_distance,
        "device": "cpu",
        "cocoindex_storage": "ok",
        "offline": True,
        "network_guard": "python_audit",
        "sqlite": sqlite3.sqlite_version,
        "dependencies": {name: version(name) for name in REPORTED_DEPENDENCIES},
    }
