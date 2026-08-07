import ast
from pathlib import Path


AGENT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = AGENT_ROOT / "agent"

ALLOWED_SETTINGS_MODULES = {
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


class _ModuleBindingCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.bindings: list[tuple[int, str]] = []

    def _record(self, line: int, name: str | None) -> None:
        if name is not None:
            self.bindings.append((line, name))

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self._record(node.lineno, node.id)

    def visit_Import(self, node: ast.Import) -> None:
        for imported in node.names:
            self._record(
                node.lineno,
                imported.asname or imported.name.split(".", maxsplit=1)[0],
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for imported in node.names:
            if imported.name != "*":
                self._record(node.lineno, imported.asname or imported.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record(node.lineno, node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._record(node.lineno, node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ListComp(self, node: ast.ListComp) -> None:
        return

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self._record(node.lineno, node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        self._record(node.lineno, node.name)
        self.generic_visit(node)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        self._record(node.lineno, node.rest)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        self._record(node.lineno, node.name)


def _module_bindings(tree: ast.Module) -> list[tuple[int, str]]:
    collector = _ModuleBindingCollector()
    collector.visit(tree)
    return collector.bindings


def _settings_boundary_violations(
    tree: ast.Module,
    *,
    relative_path: Path,
) -> list[tuple[int, str]]:
    violations: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(imported.name == "agent.config" for imported in node.names):
                violations[node.lineno] = "imports agent.config module"
        elif isinstance(node, ast.ImportFrom):
            module = _resolved_from_module(node, relative_path)
            if module is not None and any(
                f"{module}.{imported.name}" == "agent.config" for imported in node.names
            ):
                violations[node.lineno] = "imports agent.config module"
                continue
            if module != "agent.config":
                continue
            if any(imported.name == "*" for imported in node.names):
                violations[node.lineno] = "star-imports agent.config"
            elif any(imported.name == "get_settings" for imported in node.names):
                violations[node.lineno] = "imports agent.config.get_settings"

    for line, name in _module_bindings(tree):
        if name == "get_settings" and line not in violations:
            violations[line] = "defines get_settings"
    return sorted(violations.items())


def test_settings_access_stays_at_the_agent_executable_boundary() -> None:
    violations = [
        f"{relative_path}:{line}: {message}"
        for relative_path, tree in _production_modules()
        if relative_path not in ALLOWED_SETTINGS_MODULES
        for line, message in _settings_boundary_violations(
            tree,
            relative_path=relative_path,
        )
    ]

    assert violations == [], "\n".join(violations)


def test_agent_consumers_do_not_import_composition() -> None:
    violations: list[str] = []
    for relative_path, tree in _production_modules():
        if relative_path not in COMPOSITION_FREE_MODULES:
            continue
        for imported_path, line in _imported_module_paths(
            tree,
            relative_path=relative_path,
        ):
            if imported_path == "agent.composition" or imported_path.startswith(
                "agent.composition."
            ):
                violations.append(f"{relative_path}:{line}: imports {imported_path}")

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


def test_agent_settings_boundary_rejects_imports_and_module_scope_bindings() -> None:
    cases = [
        ("from agent.config import get_settings\n", [1]),
        ("from agent.config import get_settings as load\n", [1]),
        ("from agent.config import *\n", [1]),
        ("import agent.config\n", [1]),
        ("import agent.config as config\n", [1]),
        ("from agent import config as config_module\n", [1]),
        (
            "def load():\n"
            "    from agent.config import get_settings as local\n"
            "    return local()\n",
            [2],
        ),
        ("get_settings = local\n", [1]),
        ("get_settings: object\n", [1]),
        ("def get_settings():\n    return None\n", [1]),
        ("class get_settings:\n    pass\n", [1]),
        ("import compatibility as get_settings\n", [1]),
        ("from compatibility import load as get_settings\n", [1]),
        ("if enabled:\n    get_settings = local\n", [2]),
    ]

    for source, expected_lines in cases:
        assert [
            line
            for line, _message in _settings_boundary_violations(
                ast.parse(source),
                relative_path=Path("agent/session_runtime.py"),
            )
        ] == expected_lines


def test_agent_settings_boundary_allows_typed_imports_and_nested_shadows() -> None:
    tree = ast.parse(
        "from agent.config import AgentSettings\n"
        "safe = lambda get_settings: get_settings()\n"
        "values = [get_settings for get_settings in factories]\n"
        "class Consumer:\n"
        "    get_settings = local\n"
        "    def get_settings(self):\n"
        "        return None\n"
        "def use_local():\n"
        "    get_settings = local\n"
        "    return get_settings()\n"
    )

    assert (
        _settings_boundary_violations(
            tree,
            relative_path=Path("agent/session_runtime.py"),
        )
        == []
    )
