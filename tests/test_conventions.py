"""Machine-enforced monorepo conventions (see CLAUDE.md).

These tests run at the workspace root and apply to EVERY MCP server under
src/. They check conventions statically (source/AST/model introspection), so
they need no credentials and automatically cover new packages.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import re
from pathlib import Path

import pytest
from pydantic import BaseModel


SRC = Path(__file__).resolve().parent.parent / "src"

# Tool names must be verb_noun (EKS-style), starting with one of these verbs.
ALLOWED_VERBS = {
    "list",
    "get",
    "create",
    "update",
    "delete",
    "configure",
    "upgrade",
    "validate",
    "apply",
    "generate",
    "manage",
    "search",
}

# Tools starting with these prefixes mutate state and must carry a structured
# docstring. *_dryrun previews are read-only and exempt.
WRITE_PREFIXES = ("create_", "update_", "delete_", "configure_", "upgrade_")

# name may be followed by other kwargs (e.g. annotations=READ)
_TOOL_NAME_RE = re.compile(r'\.tool\(\s*name="([a-z0-9_]+)"')


def _packages() -> list[Path]:
    """Workspace members under src/ (dirs with a pyproject.toml)."""
    return sorted(p for p in SRC.iterdir() if (p / "pyproject.toml").is_file())


def _handler_files(pkg: Path) -> list[Path]:
    return sorted(pkg.glob("greennode/*/*_handler.py"))


def _registered_tools(pkg: Path) -> dict[str, Path]:
    """Map of tool name -> handler file, from `.tool(name="...")` registrations."""
    tools: dict[str, Path] = {}
    for f in _handler_files(pkg):
        for name in _TOOL_NAME_RE.findall(f.read_text()):
            tools[name] = f
    return tools


def _package_ids() -> list[str]:
    return [p.name for p in _packages()]


def test_workspace_has_packages():
    assert _packages(), "no workspace members found under src/"


@pytest.mark.parametrize("pkg_name", _package_ids())
def test_tool_names_are_verb_noun(pkg_name: str):
    """Every registered MCP tool name starts with an approved verb (verb_noun)."""
    pkg = SRC / pkg_name
    if not _handler_files(pkg):
        pytest.skip(f"{pkg_name}: library package (no MCP handlers)")
    tools = _registered_tools(pkg)
    assert tools, f"{pkg_name}: no tools registered"
    bad = [t for t in tools if t.split("_")[0] not in ALLOWED_VERBS]
    assert not bad, (
        f"{pkg_name}: tool names must be verb_noun (verbs: {sorted(ALLOWED_VERBS)}); "
        f"violations: {bad}"
    )


@pytest.mark.parametrize("pkg_name", _package_ids())
def test_write_dtos_forbid_extra(pkg_name: str):
    """Every request DTO (class name ending in 'Dto') rejects unknown fields."""
    pkg = SRC / pkg_name
    models_files = list(pkg.glob("greennode/*/models.py"))
    if not models_files:
        pytest.skip(f"{pkg_name}: no models.py")
    module_name = f"greennode.{models_files[0].parent.name}.models"
    models = importlib.import_module(module_name)
    bad = []
    for name, cls in inspect.getmembers(models, inspect.isclass):
        if not name.endswith("Dto") or not issubclass(cls, BaseModel):
            continue
        if cls.model_config.get("extra") != "forbid":
            bad.append(name)
    assert not bad, f'{pkg_name}: DTOs must set extra="forbid": {bad}'


@pytest.mark.parametrize("pkg_name", _package_ids())
def test_write_tools_have_structured_docstrings(pkg_name: str):
    """Mutating tools document '## Requirements' (per CLAUDE.md rule 10)."""
    pkg = SRC / pkg_name
    tools = _registered_tools(pkg)
    write_tools = {
        t: f
        for t, f in tools.items()
        if t.startswith(WRITE_PREFIXES) and not t.endswith("_dryrun")
    }
    if not write_tools:
        pytest.skip(f"{pkg_name}: no write tools")

    bad = []
    for handler_file in set(write_tools.values()):
        tree = ast.parse(handler_file.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            if node.name not in write_tools:
                continue
            doc = ast.get_docstring(node) or ""
            if "## Requirements" not in doc:
                bad.append(node.name)
    assert not bad, (
        f"{pkg_name}: write tools must have a '## Requirements' docstring section: "
        f"{sorted(bad)}"
    )
