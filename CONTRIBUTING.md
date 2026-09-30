# Contributing

## Development setup

Use a Python 3.12 build that supports SQLite loadable extensions, such as a uv-managed one:

```sh
uv venv --python 3.12 --managed-python .venv
uv pip install -r requirements-lock.txt
uv pip install --no-deps --no-build-isolation -e .
uv pip install --group dev
.venv/bin/delphi-code setup
```

## Code conventions

The aim is code a newcomer can read top to bottom without a guide.

**Layers.** Each module has one job, and dependencies point downward:

| Layer | Modules | Knows about |
|---|---|---|
| Entry point | `cli.py` | argparse and JSON output only; one small handler per command |
| Services | `projects.py`, `doctor.py`, `setup.py` | what a command does, expressed through domain objects |
| Domain | `store.py`, `manifest.py`, `registry.py`, `sources.py`, `hosts.py`, `model.py`, `selection.py`, `files.py`, `indexing.py` | one concept each |
| Foundations | `errors.py`, `keys.py`, `paths.py`, `offline.py` | nothing above them |

Lower layers never import `cli`, and no module below `cli` sees an `argparse.Namespace`.

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
