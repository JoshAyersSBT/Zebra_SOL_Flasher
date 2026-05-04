#!/usr/bin/env python3
"""
zebradocs_bootstrap.py

Repo-aware Sphinx bootstrapper for Python / MicroPython projects like Zebrabot.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Set, Tuple


EMBEDDED_MODULE_HINTS = {
    "machine",
    "micropython",
    "uasyncio",
    "network",
    "bluetooth",
    "esp",
    "esp32",
    "ubinascii",
    "uos",
    "utime",
    "ustruct",
    "ujson",
    "usocket",
    "neopixel",
    "framebuf",
    "rp2",
}

COMMON_EXCLUDE_DIRS = {
    ".git",
    ".github",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "build",
    "dist",
    "docs",
    "_build",
    ".mypy_cache",
    ".pytest_cache",
    ".idea",
    ".vscode",
}

SOURCE_DIR_PRIORITY = [
    "robot",
    "rtos",
    "services",
    "src",
    "lib",
    "drivers",
    "app",
]


@dataclass
class ScanResult:
    repo_root: Path
    python_files: List[Path] = field(default_factory=list)
    packages: List[Path] = field(default_factory=list)
    source_dirs: List[Path] = field(default_factory=list)
    imported_modules: Set[str] = field(default_factory=set)
    project_name: str = "Project"
    author: str = ""
    has_existing_docs: bool = False


def read_text_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1", errors="ignore")


def iter_python_files(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in COMMON_EXCLUDE_DIRS]
        current = Path(dirpath)
        for filename in filenames:
            if filename.endswith(".py"):
                yield current / filename


def extract_imports(py_path: Path) -> Set[str]:
    text = read_text_safe(py_path)
    try:
        tree = ast.parse(text, filename=str(py_path))
    except SyntaxError:
        return set()

    modules: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module.split(".")[0])
    return modules


def detect_project_name(root: Path) -> str:
    pyproject = root / "pyproject.toml"
    setup_py = root / "setup.py"
    setup_cfg = root / "setup.cfg"

    if pyproject.exists():
        text = read_text_safe(pyproject)
        match = re.search(r'(?m)^\s*name\s*=\s*["\']([^"\']+)["\']', text)
        if match:
            return match.group(1)

    if setup_cfg.exists():
        text = read_text_safe(setup_cfg)
        match = re.search(r'(?m)^\s*name\s*=\s*([^\n]+)$', text)
        if match:
            return match.group(1).strip()

    if setup_py.exists():
        text = read_text_safe(setup_py)
        match = re.search(r'name\s*=\s*["\']([^"\']+)["\']', text)
        if match:
            return match.group(1)

    return root.name


def find_packages_and_sources(root: Path, python_files: List[Path]) -> Tuple[List[Path], List[Path]]:
    package_dirs: Set[Path] = set()
    dir_scores: dict[Path, int] = {}

    for py in python_files:
        rel = py.relative_to(root)
        parent = rel.parent

        if py.name == "__init__.py":
            package_dirs.add(parent)

        if parent == Path("."):
            continue

        score = dir_scores.get(parent, 0) + 1
        if parent.name in SOURCE_DIR_PRIORITY:
            score += 10
        if (root / parent / "__init__.py").exists():
            score += 5
        dir_scores[parent] = score

    source_dirs: List[Path] = []
    for name in SOURCE_DIR_PRIORITY:
        candidate = root / name
        if candidate.exists() and candidate.is_dir() and any(candidate.rglob("*.py")):
            source_dirs.append(candidate.relative_to(root))

    if not source_dirs:
        ranked = sorted(dir_scores.items(), key=lambda item: (-item[1], str(item[0])))
        seen_top: Set[Path] = set()
        for path, _score in ranked:
            top = Path(path.parts[0]) if path.parts else path
            if top not in seen_top and top != Path("docs"):
                seen_top.add(top)
                source_dirs.append(top)
            if len(source_dirs) >= 4:
                break

    if not source_dirs and python_files:
        source_dirs = [Path(".")]

    return sorted(package_dirs), source_dirs


def scan_repo(root: Path) -> ScanResult:
    python_files = list(iter_python_files(root))
    imported: Set[str] = set()
    for py in python_files[:5000]:
        imported |= extract_imports(py)

    packages, source_dirs = find_packages_and_sources(root, python_files)

    return ScanResult(
        repo_root=root,
        python_files=python_files,
        packages=packages,
        source_dirs=source_dirs,
        imported_modules=imported,
        project_name=detect_project_name(root),
        has_existing_docs=(root / "docs").exists(),
    )


def detect_mock_imports(imports: Set[str]) -> List[str]:
    found = sorted(mod for mod in imports if mod in EMBEDDED_MODULE_HINTS)
    found.extend(["machine", "uasyncio", "network", "bluetooth"])
    return sorted(set(found))


def choose_autoapi_dirs(scan: ScanResult) -> List[str]:
    dirs = []
    for d in scan.source_dirs:
        if str(d) == ".":
            dirs.append("../")
        else:
            dirs.append(f"../{d.as_posix()}")
    return dirs or ["../"]


def to_title_case(name: str) -> str:
    return name.replace("_", " ").replace("-", " ").strip().title()


def make_conf_py(scan: ScanResult) -> str:
    project_name = scan.project_name
    display_name = to_title_case(project_name)
    autoapi_dirs = choose_autoapi_dirs(scan)
    mock_imports = detect_mock_imports(scan.imported_modules)

    autoapi_dirs_repr = "[\n" + "".join(f"    {d!r},\n" for d in autoapi_dirs) + "]"
    mock_imports_repr = "[\n" + "".join(f"    {m!r},\n" for m in mock_imports) + "]"

    return '''"""
Sphinx configuration for {display_name}.
Auto-generated by zebradocs_bootstrap.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parent
REPO_ROOT = DOCS_DIR.parent

sys.path.insert(0, str(REPO_ROOT))

project = {project_name!r}
author = {author!r}
copyright = ""
release = os.environ.get("DOCS_VERSION", "dev")

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "autoapi.extension",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]

autosummary_generate = True
autodoc_member_order = "bysource"
autoclass_content = "both"
napoleon_google_docstring = True
napoleon_numpy_docstring = True
autodoc_typehints = "description"

autodoc_mock_imports = {mock_imports_repr}

autoapi_type = "python"
autoapi_dirs = {autoapi_dirs_repr}
autoapi_keep_files = True
autoapi_options = [
    "members",
    "undoc-members",
    "show-inheritance",
    "show-module-summary",
    "imported-members",
]

myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
]

html_title = project + " documentation"
'''.format(
        display_name=display_name,
        project_name=project_name,
        author=scan.author,
        mock_imports_repr=mock_imports_repr,
        autoapi_dirs_repr=autoapi_dirs_repr,
    )


def make_index_md(scan: ScanResult) -> str:
    display_name = to_title_case(scan.project_name)
    src_list = ", ".join(d.as_posix() for d in scan.source_dirs) if scan.source_dirs else "repo root"
    return f"""# {display_name}

This documentation site was generated with a repo-aware bootstrap script for your project.

## What is included

- Automatic API documentation from your Python source
- MicroPython-friendly mocked imports for hardware-only modules
- Starter pages for architecture and development notes

## Detected source roots

`{src_list}`

```{{toctree}}
:maxdepth: 2
:caption: Contents

api
architecture
development
autoapi/index
```
"""


def make_api_md(scan: ScanResult) -> str:
    bullets = "\n".join(f"- `{d.as_posix()}`" for d in scan.source_dirs) or "- `.`"
    return f"""# API Reference

The API pages below are generated automatically from the project source tree.

## Scanned source roots

{bullets}

The full generated API appears under the **autoapi** section in the left navigation.
"""


def make_architecture_md(scan: ScanResult) -> str:
    guessed_packages = ", ".join(p.as_posix() for p in scan.packages[:12]) or "No packages detected yet"
    return f"""# Architecture Notes

Use this page to describe the major runtime pieces of the system.

## Suggested sections

- Boot sequence
- Service lifecycle
- Sensor and actuator interfaces
- Error handling and recovery
- User program execution model

## Detected packages

{guessed_packages}
"""


def make_development_md(scan: ScanResult) -> str:
    mocks = ", ".join(detect_mock_imports(scan.imported_modules)) or "None"
    return f"""# Development

## Build docs locally

```bash
pip install -r docs/requirements.txt
sphinx-build -b html docs docs/_build/html
```

## Live reload

```bash
pip install sphinx-autobuild
sphinx-autobuild docs docs/_build/html
```

## Mocked embedded imports

The docs config mocks these modules so desktop builds do not fail:

`{mocks}`
"""


def make_requirements_txt() -> str:
    return """sphinx>=8.0
sphinx-rtd-theme>=3.0
myst-parser>=4.0
sphinx-autoapi>=3.0
"""


def make_gitignore() -> str:
    return """docs/_build/
docs/autoapi/
"""


def make_make_bat() -> str:
    return r"""@ECHO OFF

pushd %~dp0

if "%SPHINXBUILD%" == "" (
    set SPHINXBUILD=sphinx-build
)
set SOURCEDIR=.
set BUILDDIR=_build

%SPHINXBUILD% -M %1 %SOURCEDIR% %BUILDDIR%
popd
"""


def make_makefile() -> str:
    return """SPHINXBUILD   ?= sphinx-build
SOURCEDIR     = .
BUILDDIR      = _build

.PHONY: html clean live

html:
\t$(SPHINXBUILD) -b html $(SOURCEDIR) $(BUILDDIR)/html

clean:
\trm -rf $(BUILDDIR) autoapi

live:
\tsphinx-autobuild $(SOURCEDIR) $(BUILDDIR)/html
"""


def append_gitignore(path: Path, new_content: str) -> str:
    if not path.exists():
        return new_content
    existing = read_text_safe(path)
    missing = []
    for line in new_content.splitlines():
        if line.strip() and line not in existing:
            missing.append(line)
    if not missing:
        return existing
    suffix = ("\n" if not existing.endswith("\n") else "") + "\n".join(missing) + "\n"
    return existing + suffix


def update_gitignore(path: Path, additions: str) -> None:
    merged = append_gitignore(path, additions)
    path.write_text(merged, encoding="utf-8")


def merge_or_write(path: Path, content: str, force: bool, dry_run: bool) -> str:
    action = "write"
    if path.exists():
        existing = read_text_safe(path)
        if existing == content:
            return f"unchanged {path}"
        action = "overwrite" if force else "skip"
        if not force:
            return f"skipped existing {path}"

    if dry_run:
        return f"{action} {path}"

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"{action} {path}"


def ensure_dirs(docs_dir: Path, dry_run: bool) -> List[str]:
    results = []
    for sub in ["_static", "_templates"]:
        p = docs_dir / sub
        if dry_run:
            results.append(f"mkdir {p}")
        else:
            p.mkdir(parents=True, exist_ok=True)
            results.append(f"mkdir {p}")
    return results


def bootstrap_docs(scan: ScanResult, force: bool = False, dry_run: bool = False) -> List[str]:
    docs_dir = scan.repo_root / "docs"
    results = ensure_dirs(docs_dir, dry_run=dry_run)

    files = {
        docs_dir / "conf.py": make_conf_py(scan),
        docs_dir / "index.md": make_index_md(scan),
        docs_dir / "api.md": make_api_md(scan),
        docs_dir / "architecture.md": make_architecture_md(scan),
        docs_dir / "development.md": make_development_md(scan),
        docs_dir / "requirements.txt": make_requirements_txt(),
        docs_dir / "Makefile": make_makefile(),
        docs_dir / "make.bat": make_make_bat(),
    }

    for path, content in files.items():
        results.append(merge_or_write(path, content, force=force, dry_run=dry_run))

    gitignore_path = scan.repo_root / ".gitignore"
    if dry_run:
        results.append(f"update {gitignore_path}")
    else:
        update_gitignore(gitignore_path, make_gitignore())
        results.append(f"update {gitignore_path}")

    return results


def print_summary(scan: ScanResult) -> None:
    print(f"Project: {scan.project_name}")
    print(f"Repo root: {scan.repo_root}")
    print(f"Python files: {len(scan.python_files)}")
    print("Detected source roots:")
    for d in scan.source_dirs:
        print(f"  - {d}")
    print("Mock imports:")
    for m in detect_mock_imports(scan.imported_modules):
        print(f"  - {m}")


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap Sphinx docs for a Python or MicroPython repo.")
    parser.add_argument("repo", nargs="?", default=".", help="Path to the project root")
    parser.add_argument("--force", action="store_true", help="Overwrite generated docs files if they already exist")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be written without changing files")
    parser.add_argument("--summary-only", action="store_true", help="Only scan and print the inferred project layout")
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    repo_root = Path(args.repo).resolve()

    if not repo_root.exists() or not repo_root.is_dir():
        print(f"Error: repo path does not exist or is not a directory: {repo_root}", file=sys.stderr)
        return 2

    scan = scan_repo(repo_root)
    print_summary(scan)

    if args.summary_only:
        return 0

    print("\nPlanned changes:" if args.dry_run else "\nApplying changes:")
    results = bootstrap_docs(scan, force=args.force, dry_run=args.dry_run)
    for item in results:
        print(f"  - {item}")

    print("\nDone.")
    print("Build with:")
    print("  pip install -r docs/requirements.txt")
    print("  sphinx-build -b html docs docs/_build/html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
