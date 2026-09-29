import hashlib
import json
import shlex
from pathlib import Path

from .errors import ExitCode, Failure


def incidental(relative):
    # Files that travel with the model but never affect embeddings: documentation, provenance,
    # and dotfile metadata left by the OS or downloader (.DS_Store, ._*, .cache).
    return relative.name in {"README.md", "LICENSE", "provenance.json"} or any(part.startswith(".") for part in relative.parts)


def inspect_model(value):
    if not value:
        raise Failure("model_missing", "Supply --model /absolute/local/model or DELPHI_CODE_MODEL", ExitCode.RUNTIME_ASSETS)
    root = Path(value).expanduser().resolve()
    if not root.is_dir() or not (root / "modules.json").is_file():
        raise Failure("model_missing", f"Local SentenceTransformers assets missing: {root}. Run delphi-code setup --model {shlex.quote(str(root))}, or set --model /local/model or DELPHI_CODE_MODEL to prepared assets; downloads are never automatic.", ExitCode.RUNTIME_ASSETS)
    modules = json.loads((root / "modules.json").read_text())
    allowed = {"sentence_transformers.models.Transformer", "sentence_transformers.models.Pooling", "sentence_transformers.models.Normalize", "sentence_transformers.models.Dense"}
    if not isinstance(modules, list) or not modules:
        raise Failure("model_invalid", "modules.json must contain a nonempty module list", ExitCode.RUNTIME_ASSETS)
    for module in modules:
        if module.get("type") not in allowed:
            raise Failure("model_invalid", f"Unsupported model module: {module.get('type')}", ExitCode.RUNTIME_ASSETS)
        folder = (root / module.get("path", "")).resolve()
        if not folder.is_relative_to(root) or (not folder.is_dir() and module["type"] != "sentence_transformers.models.Normalize"):
            raise Failure("model_missing", "A model module directory is missing or outside the model", ExitCode.RUNTIME_ASSETS)
    if not list(root.rglob("*.safetensors")):
        raise Failure("model_missing", "Model requires local safetensors weights; pickle weights are unsupported", ExitCode.RUNTIME_ASSETS)
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if incidental(relative):
            continue
        if path.is_file():
            digest.update(relative.as_posix().encode() + b"\0")
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
    return root, digest.hexdigest()


def load_model(root):
    from sentence_transformers import SentenceTransformer

    try:
        return SentenceTransformer(
            str(root), device="cpu", local_files_only=True, trust_remote_code=False,
            model_kwargs={"use_safetensors": True, "local_files_only": True},
        )
    except Exception as exc:
        raise Failure("model_invalid", f"Cannot load local model assets at {root}: {exc}", ExitCode.RUNTIME_ASSETS) from exc


def embed(model, texts):
    import numpy as np

    result = model.encode(texts, normalize_embeddings=True, show_progress_bar=False, convert_to_numpy=True)
    result = np.asarray(result, dtype=np.float32)
    if not np.isfinite(result).all() or (np.linalg.norm(result, axis=-1) == 0).any():
        raise Failure("embedding_invalid", "Model produced invalid or zero embeddings", ExitCode.RUNTIME_ASSETS)
    return result
