#!/usr/bin/env python3
"""Read-only repository inventory: what is big, what the README never mentions, what is ignored clutter.

Usage (from the repository root):  python3 scripts/repo_inventory.py [--top 12]

It deletes and changes nothing. A tracked file the README never mentions is a candidate for
review, not for deletion: some files are kept on purpose as evidence (see the README).
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_PARTS = {"tests", "__pycache__"}
SKIP_NAMES = {".gitignore", ".dockerignore", "README.md", "__init__.py", ".gitkeep", "VERSION"}


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def tree_size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=12)
    a = ap.parse_args()
    readme = (ROOT / "README.md").read_text()
    tracked = [f for f in git("ls-files").splitlines() if (ROOT / f).is_file()]
    sizes = {f: (ROOT / f).stat().st_size for f in tracked}
    print(f"== tracked files: {len(tracked)}, {human(sum(sizes.values()))}\n")
    print(f"== {a.top} largest tracked files")
    for f in sorted(sizes, key=sizes.get, reverse=True)[:a.top]:
        print(f"  {human(sizes[f]):>9}  {f}")

    print("\n== tracked files the README never mentions (by path or file name) -- review candidates")
    rows = []
    for f in tracked:
        p = Path(f)
        if p.name in SKIP_NAMES or SKIP_PARTS & set(p.parts):
            continue
        if f in readme or p.name in readme or (p.stem in readme and len(p.stem) > 6):
            continue
        last = git("log", "-1", "--format=%cs", "--", f).strip()
        rows.append((f, sizes[f], last))
    for f, sz, last in sorted(rows):
        print(f"  {human(sz):>9}  last change {last}  {f}")
    print(f"  ({len(rows)} files)")

    print("\n== ignored or untracked clutter on disk (top level, by size)")
    out = git("status", "--ignored", "--porcelain").splitlines()
    items = []
    for line in out:
        if line[:2] in ("!!", "??"):
            path = ROOT / line[3:].rstrip("/")
            if path.exists():
                items.append((tree_size(path), line[:2], line[3:]))
    for sz, flag, name in sorted(items, reverse=True)[:a.top]:
        print(f"  {human(sz):>9}  {'ignored  ' if flag == '!!' else 'UNTRACKED'}  {name}")
    print("\nnothing was changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
