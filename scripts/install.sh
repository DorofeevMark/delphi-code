#!/bin/sh
# curl -LsSf https://github.com/markdorof/delphi-code/releases/latest/download/install.sh | sh
set -eu

DELPHI_CODE_RELEASE_VERSION=""
UV_INSTALLER_URL="https://astral.sh/uv/install.sh"

say() {
  printf 'delphi-code installer: %s\n' "$1"
}

fail() {
  printf 'delphi-code installer: error: %s\n' "$1" >&2
  exit 1
}

print_usage() {
  cat <<'USAGE'
Installs delphi-code with uv, installing uv first if it is missing.

Usage: install.sh [--version VERSION] [--no-setup]

  --version VERSION  Install this release instead of the default one.
  --no-setup         Skip downloading the embedding model (run `delphi-code setup` later).

Environment: DELPHI_CODE_VERSION and DELPHI_CODE_NO_SETUP=1 work like the options.
When piping into sh, pass options after `sh -s --`.
USAGE
}

check_platform_supported() {
  operating_system=$(uname -s)
  architecture=$(uname -m)
  case "$operating_system/$architecture" in
    Darwin/arm64) ;;
    Darwin/*) fail "macOS on $architecture is not supported: PyTorch ships macOS wheels for Apple silicon only" ;;
    Linux/*) say "warning: Linux has not been verified yet; continuing" ;;
    *) fail "$operating_system is not supported" ;;
  esac
}

download_to_stdout() {
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf "$1"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- "$1"
  else
    fail "neither curl nor wget is available"
  fi
}

uv_default_install_dir() {
  if [ -n "${UV_INSTALL_DIR:-}" ]; then
    printf '%s\n' "$UV_INSTALL_DIR"
  elif [ -n "${XDG_BIN_HOME:-}" ]; then
    printf '%s\n' "$XDG_BIN_HOME"
  else
    printf '%s\n' "$HOME/.local/bin"
  fi
}

uv_executable() {
  if command -v uv >/dev/null 2>&1; then
    command -v uv
    return
  fi
  installed_uv="$(uv_default_install_dir)/uv"
  if [ ! -x "$installed_uv" ]; then
    say "uv not found; installing it from $UV_INSTALLER_URL" >&2
    download_to_stdout "$UV_INSTALLER_URL" | sh >&2 || fail "uv installation failed"
  fi
  [ -x "$installed_uv" ] || fail "uv was installed but not found at $installed_uv"
  printf '%s\n' "$installed_uv"
}

print_path_hint_if_missing() {
  case ":$PATH:" in
    *":$1:"*) ;;
    *)
      say "$1 is not on your PATH. Restart your shell, or run:"
      printf '\n  export PATH="%s:$PATH"\n\n' "$1"
      ;;
  esac
}

main() {
  requested_version="${DELPHI_CODE_VERSION:-$DELPHI_CODE_RELEASE_VERSION}"
  setup_requested=yes
  if [ -n "${DELPHI_CODE_NO_SETUP:-}" ]; then
    setup_requested=no
  fi

  while [ $# -gt 0 ]; do
    case "$1" in
      --version)
        [ $# -ge 2 ] || fail "--version needs a value"
        requested_version="$2"
        shift 2
        ;;
      --version=*)
        requested_version="${1#--version=}"
        shift
        ;;
      --no-setup)
        setup_requested=no
        shift
        ;;
      -h | --help)
        print_usage
        exit 0
        ;;
      *) fail "unknown option: $1 (see --help)" ;;
    esac
  done

  check_platform_supported
  uv=$(uv_executable)

  package_spec="delphi-code${requested_version:+==$requested_version}"
  say "installing $package_spec with $uv"
  "$uv" tool install --managed-python --upgrade "$package_spec" </dev/null

  tool_bin_dir=$("$uv" tool dir --bin)
  delphi_code="$tool_bin_dir/delphi-code"

  if [ "$setup_requested" = yes ]; then
    say "downloading and verifying the embedding model"
    "$delphi_code" setup </dev/null
  else
    say "skipped model setup; run \`delphi-code setup\` before indexing"
  fi

  print_path_hint_if_missing "$tool_bin_dir"
  say "done"
}

main "$@"
