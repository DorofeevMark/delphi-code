from dataclasses import dataclass

import cocoindex as coco
import numpy as np
from numpy.typing import NDArray
from cocoindex.connectors import sqlite
from cocoindex.ops.text import RecursiveSplitter
from cocoindex.resources.id import IdGenerator
from cocoindex.resources.schema import VectorSchema

from .errors import ExitCode, Failure
from .model import embed

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


@coco.fn(memo=True)
async def ingest(source: tuple[str, str, str], target: sqlite.TableTarget[Passage], identity: str):
    path, language, text = source
    pieces = RecursiveSplitter().split(text, chunk_size=900, min_chunk_size=180, chunk_overlap=100, language=language)
    vectors = embed(coco.use_context(MODEL), [piece.text for piece in pieces])
    ids = IdGenerator()
    for piece, vector in zip(pieces, vectors):
        end_line = piece.end.line - (1 if piece.end.column == 1 and piece.end.line > piece.start.line else 0)
        target.declare_row(row=Passage(
            await ids.next_id((piece.start.char_offset, piece.text)), path, language,
            piece.text, piece.start.line, end_line, vector,
        ))


@coco.fn
async def build(sources: dict[str, tuple[str, str, str]], identity: str, dimensions: int):
    schema = await sqlite.TableSchema.from_class(
        Passage, primary_key=["id"], column_overrides={"vector": VectorSchema(np.dtype("float32"), dimensions)},
    )
    target = await sqlite.mount_table_target(
        db=DATABASE, table_name="passages", table_schema=schema,
        virtual_table_def=sqlite.Vec0TableDef(auxiliary_columns=["path", "language", "text", "start_line", "end_line"]),
    )
    await coco.mount_each(ingest, sources.items(), target, identity)


async def run(state, sources, model, identity):
    provider = coco.ContextProvider()
    provider.provide(MODEL, model)
    connection = sqlite.connect(state / "vectors.sqlite", load_vec=True)
    provider.provide(DATABASE, connection)
    environment = storage_environment(state, provider)
    app = coco.App(coco.AppConfig(name="delphi-code", environment=environment), build, sources, identity, model.get_embedding_dimension())
    handle = app.update()
    await handle
    stats = handle.stats()
    group = stats.by_component.get("ingest") if stats else None
    return dict(group._asdict()) if group else {}


def storage_environment(state, provider=None):
    try:
        return coco.Environment(coco.Settings(db_path=state / "incremental"), context_provider=provider)
    except RuntimeError as exc:
        if "Operation not permitted" in str(exc):
            raise Failure("sandbox_storage_denied", "Sandbox denied CocoIndex storage initialization; use a sandbox allowing its native storage operations while keeping network access denied", ExitCode.RUNTIME_ASSETS) from exc
        raise


async def check_storage(state):
    storage_environment(state)
