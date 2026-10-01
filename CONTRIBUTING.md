# Contributing

## Development setup

Use a Python 3.12+ build that supports SQLite loadable extensions, such as a uv-managed one:

```sh
uv venv --managed-python .venv
uv pip install -r requirements-lock.txt
uv pip install --no-deps --no-build-isolation -e .
uv pip install --group dev
.venv/bin/delphi-code setup
```

## Code conventions

The aim is code a newcomer can read top to bottom without a guide.

**Layers.** Each folder is one layer, and dependencies point downward; `tests/test_layers.py` enforces the direction:

| Layer | Location | Knows about |
|---|---|---|
| Entry point | `cli.py` | argparse; picks the concrete UI, calls one controller, writes its data as JSON or its views as text |
| Controllers | `controllers/` | one function per command: calls services, asks through the `Prompts` port (`controllers/repository_picking.py`), and builds the views for its result |
| User interface | `ui/` | `text_layout.py` (generic terminal text), `views/` (how domain concepts look), `output.py`, `terminal.py`, `prompts.py`, `repository_list.py`; only `domain.errors` and `services.progress` below it |
| Services | `services/` | what a command does: `indexing`, `searching`, `tracking`, `project_catalog`, `model_installation`, `diagnostics`, plus the `Progress` port |
| Infrastructure | `infrastructure/` | disk, git, network and libraries: `store`, `registry`, `model`, `vector_index`, `files`, `sources`, `hosts`, `project_identity`, `model_assets`, `paths`, `offline` |
| Domain | `domain/` | pure rules with no I/O: `errors`, `keys`, `manifest`, `selection`, `tracking_changes` |

Lower layers never import `cli`, and no module below `cli` sees an `argparse.Namespace`. `infrastructure/hosts.py` and `infrastructure/model_download.py` also run as standalone scripts in a separate process, so they never import package modules unconditionally (`hosts.py` imports `domain.errors` only under `if __package__`).

**User interface.** Services, infrastructure and the domain never import `ui/` and never write to the terminal. Controllers import only `ui/views/` and `ui/text_layout.py`, to build views. They depend on two ports instead of the terminal. `Progress` (`services/progress.py`) receives events and does nothing by default; `ui/terminal.py` draws them. `Prompts` (`controllers/repository_picking.py`) asks single questions; `ui/prompts.py` answers them in the terminal. A controller decides what is asked and when, gathers every decision first, and then calls one service command such as `tracking.apply_tracking_changes`, so services never ask. Each controller returns a `ControllerResponse` with the data for JSON output and the views for text output. `cli.py` is the one place that picks port implementations and the output format.

**Interfaces.**
- Hide representations behind objects. Callers ask `manifest.ready` or `index.search(...)`; they never reach into JSON dicts or SQL.
- Pass domain types (`LocalModel`, `FileSelection`, `Checkout`, `Manifest`), not tuples or loose dicts. Dicts appear only at the JSON output edge.
- Anything a module does not export starts with `_`. Public functions and methods carry type hints.
- Constructors stay cheap; expensive work (loading the encoder, hashing) is lazy or explicit (`LocalModel.inspect`).

**Names over comments.** Make names carry the meaning and default to no comments or docstrings. Keep a comment only for a non-obvious *why* that no name can express. Replace magic values with named constants.

**Errors.** Raise `Failure(code, message, ExitCode.X)` from `errors.py`. `code` is the stable machine-readable identifier; `ExitCode` follows the table in the README. The message tells the user how to recover.

**Tests.** Test behaviour through public seams: the CLI entry points, `LocalModel.inspect` (replaced by `tests/fakes.FakeModel`), and `projects.index_checkout`. Do not patch private helpers.

**Style.** Ruff formats and lints (120 columns) and pyright type-checks the package. Run them before committing:

```sh
bash scripts/lint.sh
```

## Tests

Run the regular suite from the checkout root:

```sh
bash scripts/test.sh
```

It covers setup logic, project resolution, storage paths, model defaults, and cross-project ranking with real SQLite and mocked embeddings. It needs installed dependencies, but no prepared model or OS network sandbox, and makes no external requests; importing Delphi Code still installs its normal Python network guard. `.venv/bin/python -m unittest discover -s tests -v` runs the same suite.

Run the offline integration suite separately, on macOS with a prepared model:

```sh
export DELPHI_CODE_TEST_MODEL="$HOME/Library/Application Support/delphi-code/models/all-MiniLM-L6-v2"
bash scripts/test_offline.sh
```

Set `DELPHI_CODE_TEST_MODEL` to the actual model location; a model prepared inside the checkout can use `"$PWD/.models/all-MiniLM-L6-v2"`. Both scripts accept `DELPHI_CODE_PYTHON` to select a different Python executable.

The `tests/offline` suite launches real CLI subprocesses under OS network denial, uses an empty Hugging Face cache, and records Python DNS/connect attempts before runtime imports. It covers offline model import and reuse, missing models, doctor, actual retrieval, same-content reuse, preserved-mtime edits, deletion, ignore negation, filters, line numbers, empty results, and JSON output and exit codes. A separate socket probe verifies that the OS blocks networking. The directory is intentionally outside regular unittest discovery, so run both scripts for the full check. Neither suite tests live model downloads; CI does (see below).

## Third-party notices

For a release that bundles dependencies, run `scripts/collect_notices.py` in the release environment and `scripts/prepare_notices.py` during online preparation. Both collect notices under `build/third_party/`; review them and include them with that release. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Releasing

The `Publish` GitHub Actions workflow builds and validates distributions on every push to `main`, then installs the wheel on macOS, runs an online `delphi-code setup`, both test suites, and a smoke test outside the checkout.

To release:

1. Bump `version` in `pyproject.toml` and push to `main`.
2. Once the workflow passes, push a matching tag, for example `git tag v0.1.1 && git push origin v0.1.1`. The tag must match the package version.

The tag run repeats the checks and publishes to PyPI through a Trusted Publisher (project `delphi-code`, owner `DorofeevMark`, repository `delphi-code`, workflow `publish.yml`, environment `pypi`). Publishing uses GitHub OIDC; no stored PyPI token is needed. PyPI never accepts the same version twice.
