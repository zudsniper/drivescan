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

# pip / pipx — Ubuntu 23.04+ blocks pip system-wide (PEP 668); use pipx if available
USE_PIPX=0
if command -v pipx &>/dev/null; then
    USE_PIPX=1
    info "Using pipx for install."
elif python3 -m pip --version &>/dev/null 2>&1; then
    info "Using pip for install."
else
    err "Neither pipx nor pip found. Run: sudo apt install pipx"
    exit 1
fi

# pip flags that work on both old and new Ubuntu
PIP_FLAGS="--user --quiet"
# Ubuntu 23.04+ (PEP 668) requires this flag when not in a venv
if python3 -m pip install --help 2>&1 | grep -q 'break-system-packages'; then
    PIP_FLAGS="$PIP_FLAGS --break-system-packages"
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

if [ "$USE_PIPX" = "1" ]; then
    pipx install --force "$SRC_DIR" 2>/dev/null \
        || pipx install "$SRC_DIR"
else
    # Ensure setuptools is available for the build
    # shellcheck disable=SC2086
    python3 -m pip install $PIP_FLAGS 'setuptools>=68.0,<70.0' 'wheel' 2>/dev/null || true
    # shellcheck disable=SC2086
    python3 -m pip install $PIP_FLAGS --no-build-isolation "$SRC_DIR" 2>/dev/null \
        || python3 -m pip install $PIP_FLAGS "$SRC_DIR"
fi

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
