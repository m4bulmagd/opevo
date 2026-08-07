import ast
from importlib.util import resolve_name
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

SETTINGS_MODULE = "agent.config"
SETTINGS_IMPORT_ANCESTORS = {"agent", SETTINGS_MODULE}
SETTINGS_PACKAGE = "agent"


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


def _literal_call_argument(
    node: ast.Call,
    *,
    position: int,
    keyword: str,
) -> str | None:
    argument: ast.expr | None = (
        node.args[position] if len(node.args) > position else None
    )
    if argument is None:
        argument = next(
            (item.value for item in node.keywords if item.arg == keyword),
            None,
        )
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return argument.value
    return None


def _literal_dynamic_import(node: ast.Call) -> str | None:
    is_import_module = (
        isinstance(node.func, ast.Name) and node.func.id == "import_module"
    ) or (isinstance(node.func, ast.Attribute) and node.func.attr == "import_module")
    is_builtin_import = (
        isinstance(node.func, ast.Name) and node.func.id == "__import__"
    )
    if not (is_import_module or is_builtin_import):
        return None
    imported_name = _literal_call_argument(node, position=0, keyword="name")
    if imported_name is None or not imported_name.startswith("."):
        return imported_name
    if is_builtin_import:
        return None
    package = _literal_call_argument(node, position=1, keyword="package")
    if package is None:
        return None
    try:
        return resolve_name(imported_name, package)
    except (ImportError, ValueError):
        return None


def _settings_boundary_violations(
    tree: ast.Module,
    *,
    relative_path: Path,
) -> list[tuple[int, str]]:
    violations: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                imported.name == SETTINGS_PACKAGE
                or imported.name.startswith(f"{SETTINGS_PACKAGE}.")
                for imported in node.names
            ):
                violations[node.lineno] = (
                    f"uses module-form {SETTINGS_PACKAGE} import"
                )
        elif isinstance(node, ast.ImportFrom):
            module = _resolved_from_module(node, relative_path)
            if module == SETTINGS_PACKAGE and any(
                imported.name == "config" for imported in node.names
            ):
                violations[node.lineno] = f"imports {SETTINGS_MODULE} module"
                continue
            if module in SETTINGS_IMPORT_ANCESTORS and any(
                imported.name == "*" for imported in node.names
            ):
                violations[node.lineno] = f"star-imports {module}"
            elif module == SETTINGS_MODULE and any(
                imported.name == "get_settings" for imported in node.names
            ):
                violations[node.lineno] = f"imports {SETTINGS_MODULE}.get_settings"
        elif isinstance(node, ast.Call):
            imported_name = _literal_dynamic_import(node)
            if imported_name in SETTINGS_IMPORT_ANCESTORS:
                violations[node.lineno] = f"dynamically imports {imported_name}"

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


def test_agent_settings_boundary_rejects_package_syntax_and_literal_dynamic_imports() -> (
    None
):
    forbidden_cases = [
        ("import agent\nagent.config.get_settings()\n", [1]),
        ("import agent as package\npackage.config.get_settings\n", [1]),
        ("import agent.providers\n", [1]),
        ("from agent import config as cfg\ncfg.get_settings()\n", [1]),
        ("from agent import *\n", [1]),
        ("from . import config\n", [1]),
        (
            "def use_local():\n"
            "    import agent as package\n"
            "    package = local\n"
            "    return package\n",
            [2],
        ),
        (
            "import importlib\n"
            'importlib.import_module("agent")\n',
            [2],
        ),
        (
            "import importlib as loader\n"
            'loader.import_module("agent.config")\n',
            [2],
        ),
        (
            'import_module(".config", "agent")\n',
            [1],
        ),
        (
            'loader.import_module(name=".config", package="agent")\n',
            [1],
        ),
        ('__import__("agent.config")\n', [1]),
        ('__import__(name="agent.config")\n', [1]),
    ]

    for source, expected_lines in forbidden_cases:
        assert [
            line
            for line, _message in _settings_boundary_violations(
                ast.parse(source),
                relative_path=Path("agent/session_runtime.py"),
            )
        ] == expected_lines


def test_agent_settings_boundary_allows_livekit_and_nonliteral_dynamic_imports() -> (
    None
):
    tree = ast.parse(
        "from agent import providers\n"
        "from .config import AgentSettings\n"
        "import agentic\n"
        "import importlib as loader\n"
        'loader.import_module("livekit.plugins.turn_detector.multilingual")\n'
        'loader.import_module(f"livekit.plugins.{tts_provider}")\n'
        'loader.import_module(".providers", package="agent")\n'
        'loader.import_module("..config", package="")\n'
        'loader.import_module(".config", package_name)\n'
        'loader.import_module(name=".config", package=None)\n'
        "import_module(module_name, package=package_name)\n"
        '__import__("livekit.plugins")\n'
    )

    assert _settings_boundary_violations(
        tree,
        relative_path=Path("agent/session_runtime.py"),
    ) == []
