#!/usr/bin/env bash
# Finish a release from a Mac when the macOS CI job could not get a runner.
#
# CI's build jobs upload the Windows and Linux files to a DRAFT release and the (skipped)
# release job would do the rest. This script does that rest locally, in the same order:
#   1. upload the locally built dmg (python packaging/build.py) to the draft
#   2. download every asset, write SHA256SUMS.txt (the in-app updater verifies against it)
#   3. upload the checksums and license files, set the notes, publish as latest
#   4. build the sdist, attach it (what the Homebrew formula installs), upload to PyPI
#      when PYPI_API_TOKEN is set
#   5. update the Homebrew tap (cask + formula), pushed with the gh login
# Needs: gh (signed in: gh auth login), python with `build` (and `twine` for PyPI),
# the gh login must be allowed to push to the tap. Usage: packaging/publish_local.sh [--skip-tap] [--skip-pypi]
set -euo pipefail
cd "$(dirname "$0")/.."
REPO=${REPO:-pyhttp2/pyhttp}
TAP_REPO=${TAP_REPO:-pyhttp2/homebrew-pyhttp}
LEGACY_TAP_REPO=${LEGACY_TAP_REPO:-pyhttp2/pyhttp-releases}   # old tap URL some Macs still use: same cask file, no releases
PY=${PY:-.venv/bin/python}
VERSION=$(grep -E '^__version__' pyhttp/__init__.py | sed -E 's/.*"([^"]+)".*/\1/')
TAG="v${VERSION}"
SHA=$(git rev-parse HEAD)
SKIP_TAP=0; SKIP_PYPI=0; ONLY_TAP=0
for a in "$@"; do case "$a" in --skip-tap) SKIP_TAP=1;; --skip-pypi) SKIP_PYPI=1;; --only-tap) ONLY_TAP=1;; esac; done

gh auth status >/dev/null 2>&1 || { echo "gh is not signed in: run  gh auth login"; exit 2; }
[ "$(git rev-parse "${TAG}^{commit}" 2>/dev/null)" = "$SHA" ] || echo "note: HEAD is not the commit tagged ${TAG}; the notes will cite $SHA"
DMG="dist/pyhttp-${VERSION}-macos-arm64.dmg"
[ -f "$DMG" ] || { echo "missing $DMG: run  python packaging/build.py  first"; exit 2; }
gh release view "$TAG" --repo "$REPO" --json isDraft,assets --jq '"draft=\(.isDraft) assets=\([.assets[].name] | join(", "))"'

if [ "$ONLY_TAP" = 1 ]; then
  [ -f "dist/pyhttp_app-${VERSION}.tar.gz" ] || "$PY" -m build --sdist >/dev/null
else
echo "== 1. upload the dmg"
gh release upload "$TAG" "$DMG" --repo "$REPO" --clobber

echo "== 2. checksums over every app file in the release"
rm -rf packaging/build/release && mkdir -p packaging/build/release
gh release download "$TAG" --repo "$REPO" --dir packaging/build/release --pattern 'pyhttp-*'
( cd packaging/build/release
  ls -la
  test -f pyhttp-*-windows-x64-setup.exe || { echo "the Windows installer is missing from the draft"; exit 1; }
  test -f pyhttp-*-linux-amd64.deb || { echo "the Linux .deb is missing from the draft"; exit 1; }
  test -f pyhttp-*-macos-arm64.dmg || { echo "the dmg is missing from the draft"; exit 1; }
  shasum -a 256 pyhttp-* > SHA256SUMS.txt
  cat SHA256SUMS.txt )

echo "== 3. notes, checksums, publish"
NOTES=packaging/build/release/notes.md
cat > "$NOTES" <<'NOTES_EOF'
pyhttp @VERSION@, built from @SHA@.

- Windows 10/11: run the installer `pyhttp-@VERSION@-windows-x64-setup.exe` (per user, no admin rights, Start menu entry, uninstaller), or use the portable `pyhttp-@VERSION@-windows-x64.exe` (one file, no install). SmartScreen: More info → Run anyway.
- macOS (Apple Silicon): `brew install --cask pyhttp2/pyhttp/pyhttp`, or open the `.dmg` and drag pyhttp to Applications.
  If macOS says it cannot verify the app: System Settings → Privacy & Security → Open Anyway, or `xattr -dr com.apple.quarantine /Applications/pyhttp.app`.
- Ubuntu 22.04+ / Debian 12+: `sudo apt install ./pyhttp-@VERSION@-linux-amd64.deb` (uses the distro's GTK and WebKitGTK; other distros run from source).
- Any OS with Python 3.10+: `pip install pyhttp-app` (or `brew install pyhttp2/pyhttp/pyhttp`), then run `pyhttp`.
- Updating: pyhttp shows an **Update** button when a newer version exists. On Windows (installer or portable) and on macOS installed with Homebrew it downloads and installs the update itself and restarts; other installs get the download and the commands to run. `SHA256SUMS.txt` lists the checksums of the files above.

Free for everyone under the pyhttp Free License 1.1 (LICENSE.md): home or work, any company size, any purpose, no fees. By downloading you accept LICENSE.md. Bundled components: THIRD-PARTY-NOTICES.md.
NOTES_EOF
sed -i '' "s/@VERSION@/${VERSION}/g; s/@SHA@/${SHA}/g" "$NOTES"
gh release upload "$TAG" packaging/build/release/SHA256SUMS.txt LICENSE.md THIRD-PARTY-NOTICES.md --repo "$REPO" --clobber
gh release edit "$TAG" --repo "$REPO" --title "pyhttp ${VERSION}" --notes-file "$NOTES" --draft=false --latest
echo "published https://github.com/${REPO}/releases/tag/${TAG}"

echo "== 4. sdist (attached to the release; the formula installs it)"
# a re-run keeps the sdist already attached (PyPI holds the same file; the formula's hash must match it)
if gh release download "$TAG" --repo "$REPO" --pattern "pyhttp_app-${VERSION}.tar.gz" --dir packaging/build/release >/dev/null 2>&1 \
   && [ -f "packaging/build/release/pyhttp_app-${VERSION}.tar.gz" ]; then
  cp "packaging/build/release/pyhttp_app-${VERSION}.tar.gz" "dist/pyhttp_app-${VERSION}.tar.gz"
  echo "sdist already on the release: kept"
else
  rm -f dist/pyhttp_app-"${VERSION}".tar.gz dist/pyhttp_app-"${VERSION}"-py3-none-any.whl
  "$PY" -m pip install --quiet build
  "$PY" -m build --sdist --wheel >/dev/null
  gh release upload "$TAG" "dist/pyhttp_app-${VERSION}.tar.gz" --repo "$REPO" --clobber
fi
[ -f "dist/pyhttp_app-${VERSION}-py3-none-any.whl" ] || "$PY" -m build --wheel >/dev/null
if [ "$SKIP_PYPI" = 1 ]; then echo "PyPI: skipped (--skip-pypi)"
elif [ -n "${PYPI_API_TOKEN:-}" ]; then
  "$PY" -m pip install --quiet twine
  # --skip-existing: re-running after a partial publish must not stop here (PyPI never replaces a file)
  TWINE_USERNAME=__token__ TWINE_PASSWORD="$PYPI_API_TOKEN" "$PY" -m twine upload --non-interactive --skip-existing "dist/pyhttp_app-${VERSION}.tar.gz" "dist/pyhttp_app-${VERSION}-py3-none-any.whl"
  echo "uploaded https://pypi.org/project/pyhttp-app/${VERSION}/"
else echo "PyPI: skipped (set PYPI_API_TOKEN to upload)"; fi
fi   # ONLY_TAP

if [ "$SKIP_TAP" = 1 ]; then echo "tap: skipped (--skip-tap)"; exit 0; fi
echo "== 5. Homebrew tap: cask + formula"
DMG_SHA=$(shasum -a 256 "$DMG" | cut -d' ' -f1)
SDIST_SHA=$(shasum -a 256 "dist/pyhttp_app-${VERSION}.tar.gz" | cut -d' ' -f1)
SDIST_URL="https://github.com/${REPO}/releases/download/${TAG}/pyhttp_app-${VERSION}.tar.gz"
rm -rf packaging/build/tap
mkdir -p packaging/build/tap-files
cat > packaging/build/tap-files/pyhttp.rb <<CASK_EOF
cask "pyhttp" do
  version "${VERSION}"
  sha256 "${DMG_SHA}"

  url "https://github.com/${REPO}/releases/download/v#{version}/pyhttp-#{version}-macos-arm64.dmg"
  name "pyhttp"
  desc "Local API client with a collection runner (alternative to Postman)"
  homepage "https://pyhttp.com"

  depends_on arch: :arm64
  app "pyhttp.app"

  zap trash: "~/Library/Application Support/pyhttp"
end
CASK_EOF
for TR in "$TAP_REPO" "$LEGACY_TAP_REPO"; do
  rm -rf packaging/build/tap
  # push with the gh login (HTTPS), so the tap does not depend on which account the SSH key belongs to
  git clone -q --depth 1 "https://x-access-token:$(gh auth token)@github.com/${TR}.git" packaging/build/tap || { echo "cannot clone ${TR}; skipped"; continue; }
  mkdir -p packaging/build/tap/Casks packaging/build/tap/Formula
  cp packaging/build/tap-files/pyhttp.rb packaging/build/tap/Casks/pyhttp.rb
  if [ -f packaging/build/tap/Formula/pyhttp.rb ]; then
    sed -i '' -E "s|^  url \".*\"$|  url \"${SDIST_URL}\"|; s|^  sha256 \".*\"$|  sha256 \"${SDIST_SHA}\"|" packaging/build/tap/Formula/pyhttp.rb
  fi
  ( cd packaging/build/tap
    git add Casks/pyhttp.rb Formula/pyhttp.rb 2>/dev/null || git add Casks/pyhttp.rb
    git -c user.name="pyhttp Dev Team" -c user.email="dev@pyhttp.com" commit -q -m "pyhttp ${VERSION}: cask + formula" || { echo "tap ${TR} unchanged"; exit 0; }
    git push -q
    echo "tap updated: https://github.com/${TR}" )
done
echo "done: pyhttp ${VERSION} is published"
