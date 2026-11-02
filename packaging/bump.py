"""Release a new version in one step.

    python packaging/bump.py 0.1.2            # set the version, commit, tag v0.1.2, push both
    python packaging/bump.py 0.1.2 --no-push  # same, but leave the push to you

Updates __version__ in pyhttp/__init__.py, commits "Release 0.1.2", creates the tag
v0.1.2 and pushes main plus the tag. The pushed tag triggers the GitHub workflow,
which builds the apps and publishes them to the public releases repository.
The workflow refuses a tag whose version differs from __version__, which is the
mistake this script prevents.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "pyhttp" / "__init__.py"


def run(*cmd: str) -> str:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    push = "--no-push" not in sys.argv
    if len(args) != 1 or not re.fullmatch(r"\d+\.\d+\.\d+([.-][A-Za-z0-9.]+)?", args[0]):
        sys.exit("usage: python packaging/bump.py <version>   e.g. 0.1.2")
    version = args[0].lstrip("vV")
    tag = f"v{version}"
    if run("git", "status", "--porcelain", "--untracked-files=no"):
        sys.exit("Commit or stash your changes first; the working tree must be clean.")
    if tag in run("git", "tag", "-l", tag).split():
        sys.exit(f"Tag {tag} already exists. Pick a new version, or delete the tag: git tag -d {tag} && git push origin :refs/tags/{tag}")
    text = INIT.read_text(encoding="utf-8")
    current = re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)
    if current == version:
        print(f"__version__ is already {version}")
    else:
        INIT.write_text(re.sub(r'__version__\s*=\s*"[^"]+"', f'__version__ = "{version}"', text), encoding="utf-8")
        run("git", "add", str(INIT))
        run("git", "commit", "-m", f"Release {version}")
    run("git", "tag", "-a", tag, "-m", f"pyhttp {version}")
    print(f"version {current} -> {version}, tag {tag} created")
    if push:
        branch = run("git", "rev-parse", "--abbrev-ref", "HEAD")
        run("git", "push", "origin", branch)
        run("git", "push", "origin", tag)
        print(f"pushed; the workflow will publish {tag} to the releases repository (Actions tab shows progress)")
    else:
        print(f"not pushed; run: git push origin main && git push origin {tag}")


if __name__ == "__main__":
    main()
