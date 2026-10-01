from ..domain.errors import ExitCode, Failure
from ..domain.manifest import Manifest
from ..infrastructure.model import LocalModel
from ..infrastructure.store import Index, Store
from .local_model import open_model
from .project_catalog import optional_path_text, resolve_indexed


class SearchRequest:
    def __init__(self, query: str, paths: list[str], languages: list[str], limit: int):
        if not query.strip():
            raise Failure("usage", "Query must be nonempty", ExitCode.USAGE)
        self.query = query
        self.paths = paths
        self.languages = languages
        self.limit = limit


def search_project(store: Store, name: str, request: SearchRequest, model_location: str) -> dict:
    key, index = resolve_indexed(store, name)
    model = open_model(model_location)
    with index.lock(False):
        manifest = index.read_searchable_manifest(model)
        results = _search_index(index, manifest, request, _query_vector(model, request))
    return {
        "project": optional_path_text(manifest.project),
        "key": key,
        "index_directory": str(index.directory),
        **manifest.revision,
        "query": request.query,
        "results": results,
    }


def search_everywhere(store: Store, request: SearchRequest, model_location: str) -> dict:
    indexes, unrecognized = store.all(), store.unrecognized_directories()
    if not indexes and not unrecognized:
        raise Failure(
            "index_missing",
            "No indexes exist; run delphi-code index -p /absolute/path/to/project first",
            ExitCode.INDEX_STATE,
        )
    if unrecognized:
        raise Failure(
            "index_incompatible", f"{unrecognized[0]}: Index has no valid project identity", ExitCode.INDEX_STATE
        )
    model = open_model(model_location)
    query_vector = _query_vector(model, request)
    results, projects = [], []
    for index in indexes:
        try:
            with index.lock(False):
                manifest = index.read_searchable_manifest(model)
                project = optional_path_text(manifest.project)
                results.extend(
                    {**row, "project": project, "key": index.key}
                    for row in _search_index(index, manifest, request, query_vector)
                )
                projects.append(project or index.key)
        except Failure as exc:
            raise Failure(exc.code, f"{index.directory}: {exc}", exc.exit_code) from exc
    results.sort(
        key=lambda row: (row["distance"], row["key"], row["path"], row["start_line"], row["end_line"], row["text"])
    )
    return {"project": None, "projects": sorted(projects), "query": request.query, "results": results[: request.limit]}


def _query_vector(model: LocalModel, request: SearchRequest) -> bytes:
    return model.embed([request.query])[0].tobytes()


def _search_index(index: Index, manifest: Manifest, request: SearchRequest, query_vector: bytes) -> list[dict]:
    rows = index.search(query_vector, request.paths, request.languages, request.limit)
    for row in rows:
        if link := manifest.web_link(row["path"], row["start_line"], row["end_line"]):
            row["url"] = link
    return rows
