# Delphi Code

Offline semantic code search for agents. Delphi Code is a small Python CLI that indexes local source trees with a local embedding model and answers natural-language queries with JSON for agents and readable text in a terminal. No daemon, server, account, or cloud embedding provider.

## Quick start

Install with the script, which also installs [uv](https://docs.astral.sh/uv/) if it is missing and runs `setup`:

```sh
curl -LsSf https://github.com/markdorof/delphi-code/releases/latest/download/install.sh | sh
```

Or, with uv already installed:

```sh
uv tool install --managed-python delphi-code
delphi-code setup
```

Then index a project and search it:

```sh
delphi-code index -p /absolute/path/to/project
delphi-code search 'where are user passwords checked?'
```

`setup` downloads and verifies the embedding model once; after that, every command runs offline. Without `-p`, `search` looks through every indexed project.

## Requirements

- macOS 14+ on arm64 (tested). Linux is not yet verified; Windows is not supported.
- Python 3.12+ with SQLite loadable extensions. `--managed-python` makes uv use a Python build that supports them; `doctor` checks this.
- No GPU needed.

## Installation

The install script runs the same `uv tool install --managed-python delphi-code` and `delphi-code setup` as the uv option. Pass options after `sh -s --`: `--no-setup` skips the model download, `--version VERSION` picks a release. Running it again upgrades.

From a checkout, with uv:

```sh
uv tool install --managed-python /absolute/path/to/delphi-code
delphi-code setup
```

`setup` downloads the pinned [all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) model and checks it against bundled hashes. Running it again reuses a valid model. `--model DIR` or `DELPHI_CODE_MODEL` selects a different location. For an offline machine, import a prepared model directory:

```sh
delphi-code setup --from /absolute/path/to/all-MiniLM-L6-v2
```

### Upgrading

```sh
uv tool upgrade delphi-code
```

See [GitHub Releases](https://github.com/markdorof/delphi-code/releases) for changes. The model and indexes are kept. If a release can't reuse an old index, commands fail with `index_incompatible`; delete the index directory the error names and run `index` or `sync` again.

### Uninstalling

```sh
uv tool uninstall delphi-code
rm -rf ~/Library/Application\ Support/delphi-code
```

On Linux the data directory is `$XDG_DATA_HOME/delphi-code` or `~/.local/share/delphi-code`.

## Indexing projects

```sh
delphi-code index -p ~/src/api              # one-off; run again after changes
delphi-code add ~/src/api ~/src/web         # track local projects
delphi-code add github.com/octo/tools       # track a remote repo (no clone needed)
delphi-code add                             # pick repos from your Bitbucket/GitHub account
delphi-code sync                            # update everything tracked
```

Tracked projects live in `repos.toml` in the data directory. Remote repos are shallow-cloned to a temp dir on `sync` and skipped when unchanged; `--ref` picks a branch or tag. Only `add` and `sync` use the network.

Remote credentials: `GITHUB_TOKEN`/`GH_TOKEN` (or `gh auth token`) for GitHub; `BITBUCKET_APP_PASSWORD` (an API token works) with `BITBUCKET_EMAIL` or `BITBUCKET_USERNAME` for Bitbucket. Otherwise git's stored credentials are used.

## Commands

```sh
delphi-code doctor -p /path/to/project
delphi-code index -p /path/to/project
delphi-code search -p /path/to/project 'parse configuration' --language python --path 'src/*' --limit 5
delphi-code status -p /path/to/project
delphi-code list --indexed
```

| Command | What it does | Options |
|---|---|---|
| `setup` | Downloads or imports the model and checks the installation. | `--from DIR`, `--model DIR` |
| `doctor` | Checks the model, SQLite extensions, and storage with a real embedding. | `-p`, `--model` |
| `index` | Builds or incrementally updates a project's index. | `-p`, `--model`, `--path`, `--language`, `--ignore`, `--max-bytes` |
| `search QUERY` | Runs a natural-language query. Without `-p`, searches every index. | `-p`, `--model`, `--path`, `--language`, `--limit` (default 10) |
| `status` | Reports a project's index state. | `-p` |
| `add [SOURCE ...]` | Tracks and indexes projects or remote repositories; without sources, picks repositories interactively. | `--ref`, `--no-sync`, `--model`, `--path`, `--language`, `--ignore`, `--max-bytes` |
| `sync` | Indexes every tracked project. | `--model` |
| `list` | Lists tracked projects and stored indexes. | `--indexed` |
| `remove NAME` | Stops tracking a project and deletes its index. | `--keep-index` |

Every command accepts `--json` and `--help`. `--path`, `--language`, and `--ignore` can be repeated.

### Selecting a project

`-p` / `--project` takes a path, or, once a project is indexed, its key or folder name (`acme/api`, `api`). Use `./name` to force a directory. Without `-p`, `search` searches all indexes and other commands use the current directory.

## Output

In a terminal, commands print readable text (`NO_COLOR` disables colors). Otherwise, or with `--json`, each command writes one JSON object with `schema_version`, `ok`, `command`, and `data` or `error`:

```json
{
  "command": "search",
  "ok": true,
  "schema_version": 1,
  "data": {
    "query": "where is the model checksum verified?",
    "results": [
      {
        "path": "delphi_code/setup.py",
        "language": "python",
        "start_line": 17,
        "end_line": 31,
        "score": 0.39,
        "text": "def verify_assets(root):\n    …"
      }
    ]
  }
}
```

Higher `score` means a closer match.

| Exit code | Meaning |
|---|---|
| 0 | Success, including no matches |
| 2 | Invalid arguments or project |
| 3 | Missing or incompatible model |
| 4 | Missing, incomplete, or incompatible index |
| 5 | Busy index or operational failure |

## What gets indexed

- `.gitignore` and `.delphi-codeignore` files are respected, plus any `--ignore` patterns.
- `.git`, `.venv`, `venv`, `node_modules`, `__pycache__`, and the model directory are skipped, as are symlinks, binary files, and files over `--max-bytes` (default 1 MiB).
- `--path` globs and `--language` values (such as `python`, `typescript`) narrow the selection. On `index` they define the whole indexed set; on `search` they filter results.

## Models

The default model, MiniLM, is small and general-purpose. Any local SentenceTransformers model with safetensors weights and no custom code can be used through `--model` or `DELPHI_CODE_MODEL`. Searching an index with a different model fails instead of returning wrong results.

## Storage and logs

Data lives in `~/Library/Application Support/delphi-code/` on macOS and `~/.local/share/delphi-code/` on Linux: the model, indexes, `repos.toml`, and `logs/delphi-code.log`. Override locations with `DELPHI_CODE_MODEL`, `DELPHI_CODE_INDEX_ROOT`, `DELPHI_CODE_REGISTRY`, and `DELPHI_CODE_LOG_FILE`; set `DELPHI_CODE_LOG_LEVEL` to `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `OFF`. Logs never leave the machine.

A Git repository with an `origin` remote shares one index across clones and moves; other directories are indexed by path.

## Offline guarantees

The CLI disables telemetry, loads models only from local files, and blocks network access from Python. Only `setup`, and `add`/`sync` for remote repositories, need the network. For an OS-level guarantee on macOS:

```sh
/usr/bin/sandbox-exec -p '(version 1)(allow default)(deny network*)' \
  delphi-code index -p /path/to/project
```

## Development

See [CONTRIBUTING.md](https://github.com/markdorof/delphi-code/blob/main/CONTRIBUTING.md).

## License

Apache-2.0. See [THIRD_PARTY_NOTICES.md](https://github.com/markdorof/delphi-code/blob/main/THIRD_PARTY_NOTICES.md) for dependency and model notices.
