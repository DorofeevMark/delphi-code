from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cocoindex as coco
from cocoindex.connectors import sqlite
from cocoindex.ops.text import RecursiveSplitter
from cocoindex.resources.id import IdGenerator
from cocoindex.resources.schema import VectorSchema
import numpy as np
from numpy.typing import NDArray

from ..domain.errors import ExitCode, Failure
from .files import SourceFile
from .model import LocalModel

CHUNK_CHARACTERS = 900
MIN_CHUNK_CHARACTERS = 180
CHUNK_OVERLAP_CHARACTERS = 100

MODEL = coco.ContextKey("local_model")
DATABASE = coco.ContextKey("vector_database")


@dataclass
class Passage:
    id: int
    path: str
    language: str
    text: str
    start_line: int
    end_line: int
    vector: NDArray[np.float32]


async def build_index(
    directory: Path, files: dict[str, SourceFile], model: LocalModel, on_files_done: Callable[[int], None]
) -> dict:
    provider = coco.ContextProvider()
    provider.provide(MODEL, model)
    provider.provide(DATABASE, sqlite.connect(directory / "vectors.sqlite", load_vec=True))
    environment = _storage_environment(directory, provider)
    app = coco.App(
        coco.AppConfig(name="delphi-code", environment=environment), build, files, model.sha256, model.dimensions
    )
    ingest_stats = None
    async for snapshot in app.update().watch():
        ingest_stats = snapshot.stats.by_component.get("ingest")
        if ingest_stats:
            on_files_done(ingest_stats.num_finished)
    return dict(ingest_stats._asdict()) if ingest_stats else {}


async def check_storage(directory: Path):
    _storage_environment(directory)


@coco.fn
async def build(files: dict[str, SourceFile], model_sha256: str, dimensions: int):
    schema = await sqlite.TableSchema.from_class(
        Passage,  # pyright: ignore[reportArgumentType]
        primary_key=["id"],
        column_overrides={"vector": VectorSchema(np.dtype("float32"), dimensions)},
    )
    target = await sqlite.mount_table_target(
        db=DATABASE,
        table_name="passages",
        table_schema=schema,
        virtual_table_def=sqlite.Vec0TableDef(auxiliary_columns=["path", "language", "text", "start_line", "end_line"]),
    )
    await coco.mount_each(ingest, files.items(), target, model_sha256)  # pyright: ignore[reportCallIssue, reportArgumentType]


@coco.fn(memo=True)
async def ingest(source: SourceFile, target: sqlite.TableTarget[Passage], model_sha256: str):
    path, language, text = source
    pieces = RecursiveSplitter().split(
        text,
        chunk_size=CHUNK_CHARACTERS,
        min_chunk_size=MIN_CHUNK_CHARACTERS,
        chunk_overlap=CHUNK_OVERLAP_CHARACTERS,
        language=language,
    )
    model: LocalModel = coco.use_context(MODEL)
    vectors = model.embed([piece.text for piece in pieces])
    ids = IdGenerator()
    for piece, vector in zip(pieces, vectors, strict=True):
        ends_at_line_start = piece.end.column == 1 and piece.end.line > piece.start.line
        end_line = piece.end.line - 1 if ends_at_line_start else piece.end.line
        target.declare_row(
            row=Passage(
                await ids.next_id((piece.start.char_offset, piece.text)),
                path,
                language,
                piece.text,
                piece.start.line,
                end_line,
                vector,
            )
        )


def _storage_environment(directory: Path, provider=None):
    try:
        return coco.Environment(coco.Settings(db_path=directory / "incremental"), context_provider=provider)
    except RuntimeError as exc:
        if "Operation not permitted" in str(exc):
            raise Failure(
                "sandbox_storage_denied",
                "Sandbox denied CocoIndex storage initialization; use a sandbox allowing its native storage operations while keeping network access denied",
                ExitCode.RUNTIME_ASSETS,
            ) from exc
        raise
