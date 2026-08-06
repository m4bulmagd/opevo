import ast
from pathlib import Path


AGENT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = AGENT_ROOT / "agent"

ALLOWED_SETTINGS_CALLERS = {
    Path("agent/main.py"),
    Path("agent/config.py"),
}

COMPOSITION_FREE_MODULES = {
    Path("agent/api_client.py"),
    Path("agent/event_publisher.py"),
    Path("agent/pipeline_factory.py"),
    Path("agent/session_runtime.py"),
    Path("agent/verification_runtime.py"),
}


def _production_modules() -> list[tuple[Path, ast.Module]]:
    return [
        (path.relative_to(AGENT_ROOT), ast.parse(path.read_text()))
        for path in sorted(PACKAGE_ROOT.rglob("*.py"))
    ]


def _resolved_from_module(node: ast.ImportFrom, relative_path: Path) -> str | None:
    if node.level == 0:
        return node.module
    package_parts = list(relative_path.with_suffix("").parts[:-1])
    retained_parts = len(package_parts) - (node.level - 1)
    if retained_parts < 0:
        return None
    resolved_parts = package_parts[:retained_parts]
    if node.module is not None:
        resolved_parts.extend(node.module.split("."))
    return ".".join(resolved_parts)


def _imported_module_paths(
    tree: ast.Module,
    *,
    relative_path: Path,
) -> list[tuple[str, int]]:
    imported: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend((name.name, node.lineno) for name in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolved_from_module(node, relative_path)
            if module is None:
                continue
            imported.append((module, node.lineno))
            imported.extend(
                (f"{module}.{name.name}", node.lineno) for name in node.names
            )
    return imported


def _settings_calls(tree: ast.Module, *, relative_path: Path) -> list[int]:
    local_names = {
        imported_name.asname or imported_name.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and _resolved_from_module(node, relative_path) == "agent.config"
        for imported_name in node.names
        if imported_name.name == "get_settings"
    }
    module_names = {
        imported_name.asname or imported_name.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for imported_name in node.names
        if imported_name.name == "agent.config"
    }
    module_names.update(
        imported_name.asname or imported_name.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and _resolved_from_module(node, relative_path) == "agent"
        for imported_name in node.names
        if imported_name.name == "config"
    )
    callable_names = local_names | {
        f"{module_name}.get_settings" for module_name in module_names
    }
    return sorted(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _dotted_name(node.func) in callable_names
    )


def _dotted_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        if parent is not None:
            return f"{parent}.{node.attr}"
    return None


def test_get_settings_calls_stay_at_the_agent_executable_boundary() -> None:
    violations = [
        f"{relative_path}:{line_number}: calls get_settings"
        for relative_path, tree in _production_modules()
        if relative_path not in ALLOWED_SETTINGS_CALLERS
        for line_number in _settings_calls(tree, relative_path=relative_path)
    ]

    assert violations == [], "\n".join(violations)


def test_agent_consumers_do_not_import_composition_or_settings_globals() -> None:
    violations: list[str] = []
    for relative_path, tree in _production_modules():
        if relative_path not in COMPOSITION_FREE_MODULES:
            continue
        for imported_path, line_number in _imported_module_paths(
            tree,
            relative_path=relative_path,
        ):
            if imported_path == "agent.composition" or imported_path.startswith(
                "agent.composition."
            ):
                violations.append(
                    f"{relative_path}:{line_number}: imports {imported_path}"
                )
            if imported_path == "agent.config.get_settings":
                violations.append(
                    f"{relative_path}:{line_number}: imports agent.config.get_settings"
                )

    assert violations == [], "\n".join(violations)


def test_relative_imports_are_resolved_before_agent_boundary_checks() -> None:
    tree = ast.parse("from .composition import AgentProcessRuntime\n")

    assert _imported_module_paths(
        tree,
        relative_path=Path("agent/session_runtime.py"),
    ) == [
        ("agent.composition", 1),
        ("agent.composition.AgentProcessRuntime", 1),
    ]
