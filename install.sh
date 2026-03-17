#!/usr/bin/env bash
set -euo pipefail

REPO="https://github.com/zudsniper/drivescan.git"
INSTALL_DIR="${HOME}/.local/share/drivescan"

# --- helpers ----------------------------------------------------------------
info()  { printf '\033[1;34m[info]\033[0m  %s\n' "$*"; }
ok()    { printf '\033[1;32m[ok]\033[0m    %s\n' "$*"; }
err()   { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; }

# --- preflight --------------------------------------------------------------
# Python 3.10+
if ! command -v python3 &>/dev/null; then
    err "python3 not found. Install Python 3.10+ and re-run."
    exit 1
fi

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    err "Python 3.10+ required (found $PY_VER)."
    exit 1
fi
info "Python $PY_VER found."

# Linux-specific hints
if [ "$(uname)" = "Linux" ]; then
    info "For NTFS drive support: sudo apt install ntfs-3g"
fi

# pip
if ! python3 -m pip --version &>/dev/null; then
    err "pip not found. Install pip and re-run."
    exit 1
fi

# --- install -----------------------------------------------------------------
SRC_DIR=""

# If running from inside the repo already, use local dir; otherwise clone.
if [ -f "pyproject.toml" ] && grep -q 'drive-scanner' pyproject.toml 2>/dev/null; then
    SRC_DIR="."
    info "Installing from local directory..."
else
    info "Cloning $REPO -> $INSTALL_DIR ..."
    rm -rf "$INSTALL_DIR"
    git clone --depth 1 "$REPO" "$INSTALL_DIR"
    SRC_DIR="$INSTALL_DIR"
    info "Installing from cloned repo..."
fi

# Ensure setuptools is available for the build (use --no-build-isolation to
# avoid pulling a broken packaging version from PyPI in isolated builds).
python3 -m pip install --user --quiet 'setuptools>=68.0,<70.0' 'wheel' 2>/dev/null || true
python3 -m pip install --user --quiet --no-build-isolation "$SRC_DIR" 2>/dev/null \
    || python3 -m pip install --user --quiet "$SRC_DIR"

# pyenv needs a rehash to pick up the new script
command -v pyenv &>/dev/null && pyenv rehash 2>/dev/null || true

# --- verify ------------------------------------------------------------------
if command -v drivescan &>/dev/null; then
    ok "drivescan installed successfully."
else
    # Common pip --user bin dirs
    USER_BIN="$(python3 -m site --user-base)/bin"
    if [ -x "$USER_BIN/drivescan" ]; then
        ok "drivescan installed at $USER_BIN/drivescan"
        echo ""
        echo "    Add this to your shell profile if it's not already on PATH:"
        echo "      export PATH=\"$USER_BIN:\$PATH\""
        echo ""
    else
        err "Installation finished but drivescan binary not found."
        err "Try: python3 -m drive_scanner --help"
        exit 1
    fi
fi

echo "Usage:"
echo "  drivescan drives          List attached drives"
echo "  drivescan scan -p /mnt    Scan a path with all filters"
echo "  drivescan filters         Show available filters"
echo "  drivescan --help          Full help"
