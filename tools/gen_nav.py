#!/usr/bin/env python3
"""Generate navigation for the course's Markdown files.

For every .md file this inserts (or refreshes) two marker-delimited blocks:

* a module nav bar right after the H1, in the four main files of each module
  (README.md, exercises.md, project.md, solutions/README.md):
      <!-- nav:start --> ... <!-- nav:end -->
* a table of contents just before the first "## " heading of files with at
  least 3 sections:
      <!-- toc:start --> ... <!-- toc:end -->

The script is idempotent: re-run it after editing headings.

    python3 tools/gen_nav.py                 # whole repository
    python3 tools/gen_nav.py --check         # exit 1 if anything would change
    python3 tools/gen_nav.py --exclude modules/28-capstone
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache"}
MODULE_FILES = {
    "README.md": "Theory",
    "exercises.md": "Exercises",
    "project.md": "Project",
    "solutions/README.md": "Solutions",
}
MAX_TOC_ENTRIES = 60  # above this, show only "##" headings

NAV_RE = re.compile(r"<!-- nav:start -->.*?<!-- nav:end -->\n*", re.S)
TOC_RE = re.compile(r"<!-- toc:start -->.*?<!-- toc:end -->\n*", re.S)
FENCE_RE = re.compile(r"^(```|~~~)")
HEADING_RE = re.compile(r"^(#{2,3})\s+(.*?)\s*#*\s*$")


def slugify(text: str, seen: dict[str, int]) -> str:
    """GitHub-compatible heading anchor."""
    t = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links/images -> text
    t = re.sub(r"<[^>]+>", "", t)  # inline HTML
    t = t.replace("`", "").lower()
    t = re.sub(r"[^\w\- ]", "", t)  # keep unicode letters/digits, _, -, space
    slug = t.replace(" ", "-")
    n = seen.get(slug, 0)
    seen[slug] = n + 1
    return slug if n == 0 else f"{slug}-{n}"


def headings(body: str) -> list[tuple[int, str, str]]:
    seen: dict[str, int] = {}
    out = []
    in_fence = False
    for line in body.splitlines():
        if FENCE_RE.match(line.strip()):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            title = m.group(2)
            out.append((level, title, slugify(title, seen)))
        elif line.startswith("# ") or line.startswith("#### "):
            # H1 and H4+ still consume anchors in GitHub's numbering
            slugify(line.lstrip("#").strip(), seen)
    return out


def display(title: str) -> str:
    # Keep inline code/emphasis, drop nested links (a link inside a link breaks).
    return re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", title)


def build_toc(body: str) -> str | None:
    hs = headings(body)
    if len([h for h in hs if h[0] == 2]) < 3:
        return None
    if len(hs) > MAX_TOC_ENTRIES:
        hs = [h for h in hs if h[0] == 2]
    lines = ["<!-- toc:start -->", "**Contents**", ""]
    for level, title, slug in hs:
        indent = "  " * (level - 2)
        lines.append(f"{indent}- [{display(title)}](#{slug})")
    lines += ["", "<!-- toc:end -->", "", ""]
    return "\n".join(lines)


def module_dirs() -> list[Path]:
    return sorted(p for p in (ROOT / "modules").iterdir() if p.is_dir() and re.match(r"\d\d-", p.name))


def module_title(mdir: Path) -> str:
    readme = mdir / "README.md"
    if readme.exists():
        first = readme.read_text().splitlines()[0]
        m = re.match(r"#\s+Module\s+\d+:\s*(.*)", first)
        if m:
            return m.group(1).strip()
    return mdir.name[3:].replace("-", " ").title()


def build_nav(path: Path) -> str | None:
    rel = path.relative_to(ROOT)
    parts = rel.parts
    if len(parts) < 3 or parts[0] != "modules":
        return None
    mdir = ROOT / parts[0] / parts[1]
    inner = "/".join(parts[2:])
    if inner not in MODULE_FILES:
        return None
    up = "../" * (len(parts) - 2)  # from the file's directory to modules/
    mods = module_dirs()
    i = mods.index(mdir)
    here = Path(*parts[2:]).parent

    def link_to(target_inner: str) -> str:
        tgt = Path(target_inner)
        rel_parts = [".."] * len(here.parts) + list(tgt.parts)
        return "/".join(rel_parts)

    tabs = []
    for f, label in MODULE_FILES.items():
        if not (mdir / f).exists():
            continue
        tabs.append(f"**{label}**" if f == inner else f"[{label}]({link_to(f)})")
    prev_l = (
        f"[← {mods[i - 1].name[:2]} {module_title(mods[i - 1])}]({up}{mods[i - 1].name}/README.md)" if i > 0 else ""
    )
    next_l = (
        f"[{mods[i + 1].name[:2]} {module_title(mods[i + 1])} →]({up}{mods[i + 1].name}/README.md)"
        if i + 1 < len(mods)
        else ""
    )
    home = f"[Course home]({up}../README.md)"
    row1 = " · ".join(tabs)
    row2 = " · ".join(x for x in (prev_l, home, next_l) if x)
    return f"<!-- nav:start -->\n> {row1}  \n> {row2}\n<!-- nav:end -->\n\n"


def process(text: str, path: Path) -> str:
    text = NAV_RE.sub("", text)
    text = TOC_RE.sub("", text)
    lines = text.split("\n")
    # Insert nav after the H1 line (and the blank line after it).
    nav = build_nav(path)
    if nav and lines and lines[0].startswith("# "):
        rest = "\n".join(lines[1:]).lstrip("\n")
        text = lines[0] + "\n\n" + nav + rest
    toc = build_toc(text)
    if toc:
        # Insert before the first "## " heading outside code fences.
        in_fence = False
        offset = 0
        for line in text.split("\n"):
            if FENCE_RE.match(line.strip()):
                in_fence = not in_fence
            elif not in_fence and line.startswith("## "):
                text = text[:offset] + toc + text[offset:]
                break
            offset += len(line) + 1
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--exclude", action="append", default=[], help="path prefix (relative to repo root) to skip")
    args = ap.parse_args()
    changed = []
    for path in sorted(ROOT.rglob("*.md")):
        rel = path.relative_to(ROOT)
        if any(p in SKIP_DIRS for p in rel.parts):
            continue
        if any(str(rel).startswith(e.rstrip("/")) for e in args.exclude):
            continue
        old = path.read_text()
        new = process(old, path)
        if new != old:
            changed.append(str(rel))
            if not args.check:
                path.write_text(new)
    for c in changed:
        print(("would update " if args.check else "updated ") + c)
    return 1 if (args.check and changed) else 0


if __name__ == "__main__":
    sys.exit(main())
