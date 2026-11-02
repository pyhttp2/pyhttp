#!/usr/bin/env sh
# Starts pyhttp on macOS / Linux. Creates a virtualenv on first use.
set -e
cd "$(dirname "$0")"
PY=${PYTHON:-python3}
if [ ! -x ".venv/bin/python" ]; then
    echo "Creating virtual environment..."
    if [ "$(uname)" = "Linux" ]; then
        # Lets the venv see the distro's PyGObject/WebKitGTK bindings for the native window.
        "$PY" -m venv --system-site-packages .venv
    else
        "$PY" -m venv .venv
    fi
fi
# Reinstall dependencies whenever requirements.txt changes.
if ! cmp -s requirements.txt .venv/installed-requirements.txt; then
    echo "Installing dependencies..."
    .venv/bin/python -m pip install -q -r requirements.txt
    cp requirements.txt .venv/installed-requirements.txt
fi
exec .venv/bin/python -m pyhttp "$@"
