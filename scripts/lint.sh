set -eu
: "${DELPHI_CODE_PYTHON:=.venv/bin/python}"
"$DELPHI_CODE_PYTHON" -m ruff format --check .
"$DELPHI_CODE_PYTHON" -m ruff check .
"$DELPHI_CODE_PYTHON" -m pyright
