#!/usr/bin/env python3
"""Scaffold a new GreenNode MCP server under src/<product>-mcp-server.

Usage:
    uv run python scripts/new_server.py <product>          # e.g. vdb
    uv run python scripts/new_server.py <product> --force  # overwrite existing

Generates a working package from templates/new-server (conventions baked in:
mcp_core imports, verb_noun example tool, structured output, tests, Dockerfile),
registers it with release-please, and prints the remaining manual steps.

Stdlib-only on purpose — no dependencies to install before scaffolding.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "templates" / "new-server"

PRODUCT_RE = re.compile(r"^[a-z][a-z0-9-]*[a-z0-9]$")

# Path-segment placeholder (double-underscore style keeps template paths importable-ish)
PATH_PLACEHOLDER = "__product_snake__"


def _substitutions(product: str) -> dict[str, str]:
    snake = product.replace("-", "_")
    return {
        "{{product}}": product,  # vdb            (dirs, CLI, dist name)
        "{{product_snake}}": snake,  # vdb            (import package segment)
        "{{Product}}": "".join(
            p.capitalize() for p in re.split(r"[-_]", product)
        ),  # Vdb
        "{{PRODUCT}}": product.upper(),  # VDB            (prose)
    }


def render(product: str, force: bool) -> Path:
    """Copy the template tree into src/, applying substitutions. Returns the target dir."""
    subs = _substitutions(product)
    target = REPO_ROOT / "src" / f"{product}-mcp-server"

    if target.exists():
        if not force:
            sys.exit(f"error: {target} already exists (use --force to overwrite)")
        shutil.rmtree(target)

    for src in sorted(TEMPLATE_DIR.rglob("*")):
        rel = src.relative_to(TEMPLATE_DIR)
        rel_str = str(rel).replace(PATH_PLACEHOLDER, subs["{{product_snake}}"])
        dst = target / rel_str
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        text = src.read_text()
        for key, value in subs.items():
            text = text.replace(key, value)
        dst.write_text(text)
        dst.chmod(src.stat().st_mode)

    return target


def register_release_please(product: str) -> None:
    """Add the new package to release-please config + manifest (idempotent)."""
    pkg_path = f"src/{product}-mcp-server"

    cfg_file = REPO_ROOT / "release-please-config.json"
    cfg = json.loads(cfg_file.read_text())
    if pkg_path not in cfg["packages"]:
        cfg["packages"][pkg_path] = {
            "release-type": "python",
            "package-name": f"{product}-mcp-server",
            "component": f"{product}-mcp-server",
            "include-component-in-tag": True,
            "bump-minor-pre-major": True,
            "bump-patch-for-minor-pre-major": False,
            "changelog-path": "CHANGELOG.md",
        }
        cfg_file.write_text(json.dumps(cfg, indent=2) + "\n")

    manifest_file = REPO_ROOT / ".release-please-manifest.json"
    manifest = json.loads(manifest_file.read_text())
    if pkg_path not in manifest:
        manifest[pkg_path] = "0.0.0"
        manifest_file.write_text(json.dumps(manifest, indent=2) + "\n")


def _lint_fix(target: Path) -> None:
    """Auto-fix import order/formatting in the generated package.

    Import sort order depends on the product name (greennode.<product> vs
    greennode.mcp_core), so it cannot be fixed statically in the template.
    """
    for cmd in (["ruff", "check", "--fix", "-q", "."], ["ruff", "format", "-q", "."]):
        try:
            subprocess.run(["uv", "run", *cmd], cwd=target, check=True)
        except (OSError, subprocess.CalledProcessError):
            print(f"warning: could not run 'uv run {' '.join(cmd)}' — run it manually")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "product", help="Product short name, e.g. 'vdb' (lowercase, hyphens ok)"
    )
    parser.add_argument(
        "--force", action="store_true", help="Overwrite an existing package"
    )
    args = parser.parse_args()

    product = args.product.lower().removesuffix("-mcp-server")
    if not PRODUCT_RE.match(product):
        sys.exit(f"error: invalid product name '{product}' (lowercase alnum + hyphens)")

    target = render(product, args.force)
    register_release_please(product)
    _lint_fix(target)

    print(f"Scaffolded {target.relative_to(REPO_ROOT)}")
    print(
        f"""
Next steps:
  1. uv sync --all-packages --all-groups
  2. cd src/{product}-mcp-server && uv run pytest tests/ -v   # scaffold tests pass out of the box
  3. Fill in the real API base URLs in config.py (REGIONS)
  4. Replace ExampleHandler with real handlers (TDD; see repo-root CLAUDE.md)
  5. Add a deploy job/tag mapping for this package in .github/workflows/deploy.yml
  6. Add '/src/{product}-mcp-server/ @<team>' to .github/CODEOWNERS
  7. Record product API quirks in src/{product}-mcp-server/CLAUDE.md
CI (lint/test/build) and release-please pick the package up automatically."""
    )


if __name__ == "__main__":
    main()
