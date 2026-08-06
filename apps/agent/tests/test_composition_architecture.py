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


class _BindingAnalyzer(ast.NodeVisitor):
    def __init__(self, *, relative_path: Path) -> None:
        self.relative_path = relative_path
        self.bindings: dict[str, str] = {}
        self.calls: list[tuple[int, str]] = []

    def _resolve(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return self.bindings.get(node.id)
        if isinstance(node, ast.Attribute):
            parent = self._resolve(node.value)
            if parent is not None:
                return f"{parent}.{node.attr}"
        return None

    def _bind_target(self, target: ast.expr, value: ast.expr) -> None:
        if not isinstance(target, ast.Name):
            return
        resolved = self._resolve(value)
        if resolved is None:
            self.bindings.pop(target.id, None)
        else:
            self.bindings[target.id] = resolved

    def visit_Import(self, node: ast.Import) -> None:
        for imported_name in node.names:
            if imported_name.asname is not None:
                self.bindings[imported_name.asname] = imported_name.name
            else:
                root_name = imported_name.name.split(".", maxsplit=1)[0]
                self.bindings[root_name] = root_name

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = _resolved_from_module(node, self.relative_path)
        if module is None:
            return
        for imported_name in node.names:
            if imported_name.name == "*":
                if module == "agent.config":
                    self.bindings["get_settings"] = "agent.config.get_settings"
                continue
            local_name = imported_name.asname or imported_name.name
            self.bindings[local_name] = f"{module}.{imported_name.name}"

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self._bind_target(target, node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
            self._bind_target(node.target, node.value)

    def visit_Call(self, node: ast.Call) -> None:
        resolved = self._resolve(node.func)
        if resolved is not None:
            self.calls.append((node.lineno, resolved))
        self.generic_visit(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.bindings.pop(node.name, None)
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                self.visit(default)

        outer_bindings = self.bindings
        self.bindings = outer_bindings.copy()
        arguments = [
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ]
        if node.args.vararg is not None:
            arguments.append(node.args.vararg)
        if node.args.kwarg is not None:
            arguments.append(node.args.kwarg)
        for argument in arguments:
            self.bindings.pop(argument.arg, None)
        for statement in node.body:
            self.visit(statement)
        self.bindings = outer_bindings

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.bindings.pop(node.name, None)
        for expression in [*node.decorator_list, *node.bases]:
            self.visit(expression)
        for keyword in node.keywords:
            self.visit(keyword.value)

        outer_bindings = self.bindings
        self.bindings = outer_bindings.copy()
        class_bindings = self.bindings
        for statement in node.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.bindings = outer_bindings.copy()
                self.visit(statement)
                self.bindings = class_bindings
            else:
                self.visit(statement)
        self.bindings = outer_bindings


def _settings_calls(tree: ast.Module, *, relative_path: Path) -> list[int]:
    analyzer = _BindingAnalyzer(relative_path=relative_path)
    analyzer.visit(tree)
    return sorted(
        line_number
        for line_number, resolved in analyzer.calls
        if resolved == "agent.config.get_settings"
    )


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


def test_settings_call_analysis_follows_only_relevant_agent_bindings() -> None:
    cases = [
        (
            "from agent.config import get_settings as load_settings\n"
            "again = load_settings\n"
            "again()\n",
            [3],
        ),
        (
            "import agent.config as config\n"
            "load = config.get_settings\n"
            "load()\n",
            [3],
        ),
        (
            "import agent.config\n"
            "agent.config.get_settings()\n",
            [2],
        ),
        (
            "from agent.config import *\n"
            "get_settings()\n",
            [2],
        ),
        (
            "from agent.config import get_settings\n"
            "class RuntimeConsumer:\n"
            "    def load(self):\n"
            "        return get_settings()\n",
            [4],
        ),
        (
            "def get_settings():\n"
            "    return None\n"
            "get_settings()\n",
            [],
        ),
    ]

    for source, expected_lines in cases:
        assert _settings_calls(
            ast.parse(source),
            relative_path=Path("agent/session_runtime.py"),
        ) == expected_lines
