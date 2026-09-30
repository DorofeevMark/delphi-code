# Delphi Code

Offline semantic code search for agents. Delphi Code is a small Python CLI that indexes local source trees with a local embedding model and answers natural-language queries with JSON. It runs one command at a time, with no daemon, server, account, cloud embedding provider, or runtime downloads.

## Quick start

```sh
uv tool install --managed-python delphi-code
delphi-code setup
delphi-code index -p /absolute/path/to/project
delphi-code search 'where are user passwords checked?'
```

`setup` downloads and verifies the pinned embedding model once. After that, every command runs fully offline. Without `-p`, `search` looks through every indexed project.

## Requirements

- macOS 14 or later on arm64 (the tested target). Linux has not yet been verified; Windows is not supported by the current file locking implementation.
- CPython 3.12 or later (tested on 3.12, 3.13 and 3.14) with SQLite loadable extensions. Some python.org macOS builds disable that feature; `--managed-python` makes uv use its own Python build, which supports it. `doctor` reports this explicitly.
- CPU inference; no GPU is needed.

## Installation

From PyPI, as in the quick start:

```sh
uv tool install --managed-python delphi-code
delphi-code setup
```

From a checkout, with uv:

```sh
uv tool install --managed-python /absolute/path/to/delphi-code
delphi-code setup
```

From a checkout, with the pinned dependency versions and a Python 3.12+ build that supports SQLite extensions:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python -m pip install --no-deps --no-build-isolation .
.venv/bin/delphi-code setup
```

`requirements-lock.txt` records the tested macOS environment, not a cross-platform lock.

### Model setup

`setup` downloads the pinned [all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) revision in a separate process. It validates every file against bundled SHA-256 hashes and runs local embedding, SQLite, and native-storage diagnostics before moving the model into place.

- Running `setup` again verifies and reuses valid assets.
- Concurrent setup for the same destination fails clearly.
- Failed downloads or diagnostics never leave a partial model behind; invalid existing models are preserved with recovery instructions.
- `setup --model /absolute/path` or `DELPHI_CODE_MODEL` selects a different destination.

For an offline installation, install dependencies from a wheelhouse prepared for the target platform, then import a complete prepared MiniLM directory, including its license and model card:

```sh
delphi-code setup --from /absolute/path/to/all-MiniLM-L6-v2
```

`setup` provisions the pinned MiniLM model only. Other models can be used at runtime through `--model` or `DELPHI_CODE_MODEL` (see Models below). `index`, `search`, `status`, and `doctor` never download anything.

## Commands

```sh
delphi-code doctor -p /path/to/project
delphi-code index -p /path/to/project
delphi-code search -p /path/to/project 'where are user passwords checked?'
delphi-code search -p /path/to/project 'parse configuration' --language python --path 'src/*' --limit 5
delphi-code status -p /path/to/project
```

- **`index`** builds or incrementally updates a project's index.
- **`search`** runs a natural-language query. Without `-p`, it searches every stored index.
- **`status`** reports stored index state. It needs no model and does not rescan source files to detect staleness.
- **`doctor`** checks the model, SQLite extensions, and native storage, then runs a real local embedding and vector distance calculation.
- **`setup`** provisions the model (see above).
- **`add`**, **`sync`**, **`list`**, and **`remove`** manage tracked projects (see below).

`python -m delphi_code` works as an alternative to the `delphi-code` executable.

### Selecting a project

- `--project` / `-p` accepts a path, resolved relative to the current working directory. The project root is explicit; it is not inferred from Git.
- After a project has been indexed once, its key or folder name works too, for example `delphi-code search -p acme/api 'parse config'` or `-p myapp`. A key matches in full or by a trailing part (`bitbucket.org/acme/api`, `acme/api`, or `api`). A name that matches several indexes produces an error listing their keys. Use `./name` or an absolute path to select a directory explicitly.
- Without `--project`, `search` searches all stored indexes; other commands use the current directory.

### Tracking projects

Instead of indexing projects one by one, keep a list of them and update them all together:

```sh
delphi-code add ~/src/api ~/src/web --path 'src/*'
delphi-code sync
delphi-code list
delphi-code remove api
```

- **`add`** records projects and indexes them; `--no-sync` only records them. Filter options are stored with each project, and adding a project again replaces its options.
- **`sync`** indexes every tracked project. One failing project does not stop the rest: the command then exits with `sync_failed` (code 5) and still reports each project's result under `data`.
- **`list`** shows tracked projects and every stored index, including ones created with plain `index`. It needs no model.
- **`remove`** stops tracking a project and deletes its index; `--keep-index` keeps the index.

The list lives in `repos.toml` in the storage directory (`DELPHI_CODE_REGISTRY` overrides the location). You can edit it by hand; `add` and `remove` rewrite it without comments.

```toml
[[repo]]
source = "/Users/me/src/api"
key = "bitbucket.org/acme/api"
paths = ["src/*"]

[[repo]]
source = "bitbucket.org/acme/billing"
ref = "release"
```

### Bitbucket Cloud and GitHub repositories

Repositories you have not cloned can be tracked too:

```sh
delphi-code add bitbucket.org/acme/billing
delphi-code add git@bitbucket.org:acme/billing.git --ref release
delphi-code add github.com/octo/tools
```

- `sync` asks the hosting service for the latest commit of the branch or tag, which is the default branch unless `--ref` names another. It skips the repository when the index already holds that commit with the same filters and model. Otherwise it makes a shallow clone into a temporary directory, indexes it, and deletes the clone. Unchanged files are not embedded again.
- Search results from these repositories carry a `url`: a Bitbucket or GitHub link to the indexed commit and line range. The response also includes `ref` and `commit`, and `project` is `null`.
- Use `sync`, not `index`, to update them.
- Only `add` and `sync` touch the network. `index`, `search`, `status`, and `list` stay offline.

**Picking repositories.** Run `add` without sources in a terminal to choose from the repositories your accounts can see:

```sh
delphi-code add
```

Choose Bitbucket or GitHub (asked only when both have credentials), then a Bitbucket workspace or GitHub account (skipped when there is only one). If your Bitbucket credentials may not list workspaces, you type the workspace name instead; `BITBUCKET_WORKSPACE` pre-fills it. Then check repositories in a list you can filter by typing. Repositories you already track start checked, so unchecking one stops tracking it and deletes its index. After you confirm, the new repositories are indexed. Filter options and `--ref` apply to the repositories you add. The prompts are drawn on stderr, and stdout still receives one JSON object with the added repositories under `repos` and the untracked ones under `removed`. Cancelling changes nothing and reports `"cancelled": true`. Without a terminal, `add` requires sources.

**Credentials.** Git never prompts. Credentials from the environment reach git through `GIT_ASKPASS`, never in URLs or arguments, and take precedence over configured git credentials. Listing repositories for the picker uses the services' REST APIs. The API requests run in a separate process, like the model download in `setup`, so the main process keeps its network guard.

| | Bitbucket Cloud | GitHub |
|---|---|---|
| Cloning | `BITBUCKET_USERNAME` and `BITBUCKET_APP_PASSWORD`, otherwise git's own credentials | `GITHUB_TOKEN` or `GH_TOKEN`, otherwise git's own credentials (`gh auth setup-git` configures them) |
| Listing for the picker | The same variables (with `BITBUCKET_EMAIL` in place of the username when set), otherwise the credentials git has stored for bitbucket.org | `GITHUB_TOKEN` or `GH_TOKEN`, otherwise `gh auth token`, otherwise the token git has stored for github.com |

Bitbucket has replaced app passwords with API tokens. An API token also works in `BITBUCKET_APP_PASSWORD`. Git over HTTPS takes the username that Bitbucket shows for it, and the REST API takes your Atlassian account email, which you set in `BITBUCKET_EMAIL`. Cloning and listing a workspace's repositories need the `read:repository:bitbucket` scope. Listing your workspaces also needs `read:workspace:bitbucket`; without it, the picker asks for the workspace name. An error names the credentials that were used and the scopes Bitbucket requires.

`DELPHI_CODE_BITBUCKET_GIT_BASE` and `DELPHI_CODE_GITHUB_GIT_BASE` send clones to another base URL, such as a mirror, and `DELPHI_CODE_BITBUCKET_API_BASE` and `DELPHI_CODE_GITHUB_API_BASE` do the same for the APIs. Result links still point to bitbucket.org and github.com.

### Searching across projects

Cross-project search returns a single globally ranked list, with the `key` and full `project` path on each result (`null` for Bitbucket and GitHub repositories, whose results carry a `url` instead). The query is embedded once, `--limit` applies to the combined list, and path/language filters apply within every index. All indexes must be readable, complete, and built with the selected model: a busy, broken, or incompatible index produces an error rather than silently partial results. Use `-p` to narrow the search when needed.

## Output

Each command writes one JSON object to stdout with `schema_version`, `ok`, `command`, and either `data` or `error`. Progress and library diagnostics go to stderr; argument help is plain text.

```json
{
  "command": "search",
  "ok": true,
  "schema_version": 1,
  "data": {
    "project": "/Users/me/src/delphi-code",
    "index_directory": "/Users/me/Library/Application Support/delphi-code/indexes/3a3565ee…",
    "query": "where is the model checksum verified?",
    "results": [
      {
        "path": "delphi_code/setup.py",
        "language": "python",
        "start_line": 17,
        "end_line": 31,
        "distance": 1.1042553186416626,
        "score": 0.3903100956258001,
        "text": "def verify_assets(root):\n    manifest = json.loads(…"
      }
    ]
  }
}
```

Search results contain project-relative paths, language, inclusive 1-based line ranges, the original chunk text, Euclidean distance, and cosine similarity score. Lower distance and higher score mean closer matches; scores are not probabilities. Equal distances use stable path/line/id ordering.

| Exit code | Meaning |
|---|---|
| 0 | Success, including a search with no matches |
| 2 | Invalid arguments or project |
| 3 | Missing or incompatible model or runtime assets |
| 4 | Missing, incomplete, or incompatible index |
| 5 | Busy index or operational failure |

## Indexing and filters

### What gets indexed

- Nested `.gitignore` and `.delphi-codeignore` files apply with Git-style patterns and negation; an ignored parent directory is not traversed. `.delphi-codeignore` follows `.gitignore` within each directory.
- Repeated `--ignore` patterns add another exclusion layer.
- Git's global ignore file and `.git/info/exclude` are not read.
- Built-in exclusions: `.git`, `.delphi-code`, `.venv`, `venv`, `node_modules`, `__pycache__`, `.models`, and the selected model directory.
- Symlinks, binary or non-UTF-8 files, blank files, and files over `--max-bytes` (default 1 MiB) are skipped.
- Unknown languages are indexed as `text`.

### Filters

Repeated `--path` globs are alternatives over project-relative POSIX paths (`*` can match `/`). Repeated `--language` values are alternatives using CocoIndex language names such as `python`, `rust`, and `typescript`. Path and language filters combine with AND.

- On `index`, filters define the entire indexed set for that run, and previously indexed files outside it are removed. Running `index` without filters returns to the full selection.
- On `search`, filters restrict results before ranking, so a matching result cannot be lost behind unfiltered top-k candidates.

### Incremental updates

CocoIndex memoizes each file's content-dependent work and maintains the exported SQLite vec0 rows. Changed contents are detected even when timestamps are unchanged, and deleted or newly excluded files have their rows removed. Every candidate file is read on each `index` run, but unchanged files skip chunking and embedding. The selected file contents are held in memory during indexing.

### Chunking and search

The code splitter targets 900-byte chunks with overlap. Embeddings are normalized SentenceTransformers outputs computed on the CPU, with the same model configuration for indexing and queries. The model's token limit can truncate long chunks.

Search is an exact sqlite-vec distance scan over the filtered rows, not an approximate-nearest-neighbor index. That keeps results exact and suits small codebases.

## Models

The default model is MiniLM: a small, general-purpose model that keeps the tool fast and light, not one tuned for code retrieval. Any local SentenceTransformers model can be used through `--model /absolute/local/model` or `DELPHI_CODE_MODEL`, provided that it:

- uses only Transformer, Pooling, Normalize, and Dense modules;
- stores its weights as safetensors (pickle weights and custom model code are not supported);
- is trusted and stays unchanged while a command runs.

Models with asymmetric query/document instructions are not specially configured. Each index records a hash of the full model assets, so searching an index with different weights or configuration fails with a clear error instead of returning wrong results.

## Storage

Models and indexes live outside the installation, so reinstalling or upgrading leaves them intact:

- macOS: `~/Library/Application Support/delphi-code/`
- Linux: `$XDG_DATA_HOME/delphi-code/`, or `~/.local/share/delphi-code/`

The default model is under `models/all-MiniLM-L6-v2/` and indexes are under `indexes/`. `DELPHI_CODE_INDEX_ROOT` overrides the index location.

Each project has one index in `indexes/<id>/`, where the id is random. The index holds CocoIndex's incremental state, the SQLite vector table, and a manifest recording the project's key and its last indexed path. Command output includes this `index_directory` and the `key`.

A project's key decides which index it uses:

- The root of a Git repository with an `origin` remote is keyed by that remote, normalized to `host/owner/repo`. `git@bitbucket.org:acme/api.git` and `https://bitbucket.org/acme/api` both become `bitbucket.org/acme/api`. Moving or re-cloning the repository keeps its index, and unchanged files are not embedded again.
- Any other directory, including a subdirectory of a repository, is keyed by its resolved path: `local:/path/to/project`.
- Indexes created by earlier versions are given a key when they are first seen, without being rebuilt. If another index already has that remote key, the older index keeps a path key.

- A file lock prevents reads during updates and concurrent writers.
- An interrupted or failed update leaves the index marked `ready: false`, and search refuses it until indexing succeeds. There is no fallback to a last good snapshot.
- Two checkouts of the same repository share one index, which holds whichever of them was indexed last. Select a subdirectory to keep them apart.
- Moving a project that has no Git remote changes its key, and indexing it again builds a new index.
- To switch model assets or rebuild incompatible state, move that `index_directory` aside and run `index` again.

## Offline guarantees

Before any third-party import, the CLI disables CocoIndex usage tracking and Hugging Face telemetry, and sets the Hub and Transformers offline flags. Models load only from an existing local directory with `local_files_only=True`, `trust_remote_code=False`, and safetensors weights.

A Python audit hook also rejects Internet socket operations and DNS lookups. This is defense in depth, not OS isolation: it does not cover native extensions. For an OS-level boundary on macOS, run commands under a sandbox that denies networking:

```sh
/usr/bin/sandbox-exec -p '(version 1)(allow default)(deny network*)' \
  delphi-code index -p /path/to/project
```

Delphi Code needs read access to source and model files, write access to its user index directory, threads, SQLite extensions, and CocoIndex's LMDB memory maps. It needs no network access, except for `setup` and for `add` and `sync` of Bitbucket or GitHub repositories; local projects never need it. Some stricter sandboxes deny CocoIndex's native storage initialization with `EPERM`; `doctor` probes for this and reports `sandbox_storage_denied`. The application never escalates its own permissions. `doctor` reports the Python guard only; it does not claim that an external OS sandbox is active.

## Development

See [CONTRIBUTING.md](https://github.com/DorofeevMark/delphi-code/blob/main/CONTRIBUTING.md) for running the tests and publishing releases.

## License

Apache-2.0. See [THIRD_PARTY_NOTICES.md](https://github.com/DorofeevMark/delphi-code/blob/main/THIRD_PARTY_NOTICES.md) for dependency and model provenance and redistribution notes. The package does not bundle dependencies or model weights; the model's asset hashes are recorded in [MODEL_PROVENANCE.json](https://github.com/DorofeevMark/delphi-code/blob/main/MODEL_PROVENANCE.json).
