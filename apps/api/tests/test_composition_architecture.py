import ast
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = API_ROOT / "app"

ALLOWED_SETTINGS_CALLERS = {
    Path("app/main.py"),
    Path("app/workers/arq_worker.py"),
    Path("app/core/config.py"),
}

FORBIDDEN_GLOBAL_IMPORTS = {
    ("app.core.database", "get_engine"),
    ("app.core.database", "get_session_factory"),
    ("app.core.redis", "get_redis_client"),
    ("app.providers.storage.s3", "get_s3_storage"),
    ("app.providers.telephony.telnyx", "get_telephony_provider"),
}

FORBIDDEN_GLOBAL_DEFINITIONS = {
    Path("app/core/database.py"): {"get_engine", "get_session_factory"},
    Path("app/core/redis.py"): {"get_redis_client"},
    Path("app/core/observability.py"): {"get_observability"},
    Path("app/providers/storage/s3.py"): {"get_s3_storage"},
    Path("app/providers/telephony/telnyx.py"): {"get_telephony_provider"},
}

APPLICATION_DEPENDENCY_KEYS = {
    "session_factory",
    "observability",
    "outbox_handlers",
    "telephony_provider",
    "subscription_provider",
    "livekit_dispatch_provider",
    "summary_provider",
    "storage_provider",
    "recording_reconciler",
}


def _production_modules() -> list[tuple[Path, ast.Module]]:
    return [
        (path.relative_to(API_ROOT), ast.parse(path.read_text()))
        for path in sorted(APP_ROOT.rglob("*.py"))
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


def _imported_names(
    tree: ast.Module,
    *,
    relative_path: Path,
) -> list[tuple[str, str, int]]:
    imported: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = _resolved_from_module(node, relative_path)
        if module is None:
            continue
        imported.extend(
            (module, imported_name.name, node.lineno)
            for imported_name in node.names
        )
    return imported


def _imported_module_paths(
    tree: ast.Module,
    *,
    relative_path: Path,
) -> list[tuple[str, int]]:
    imported: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend((imported_name.name, node.lineno) for imported_name in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolved_from_module(node, relative_path)
            if module is None:
                continue
            imported.append((module, node.lineno))
            imported.extend(
                (f"{module}.{imported_name.name}", node.lineno)
                for imported_name in node.names
            )
    return imported


class _BindingAnalyzer(ast.NodeVisitor):
    def __init__(self, *, relative_path: Path) -> None:
        self.relative_path = relative_path
        self.bindings: dict[str, str] = {}
        self.calls: list[tuple[int, str]] = []
        self.attributes: list[tuple[int, str]] = []

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
                if module == "app.core.config":
                    self.bindings["get_settings"] = "app.core.config.get_settings"
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

    def visit_Attribute(self, node: ast.Attribute) -> None:
        resolved = self._resolve(node)
        if resolved is not None:
            self.attributes.append((node.lineno, resolved))
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


def _analyze_bindings(tree: ast.Module, *, relative_path: Path) -> _BindingAnalyzer:
    analyzer = _BindingAnalyzer(relative_path=relative_path)
    analyzer.visit(tree)
    return analyzer


def _settings_calls(tree: ast.Module, *, relative_path: Path) -> list[int]:
    analyzer = _analyze_bindings(tree, relative_path=relative_path)
    return sorted(
        line_number
        for line_number, resolved in analyzer.calls
        if resolved == "app.core.config.get_settings"
    )


def _assignment_target_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    if isinstance(node, ast.AnnAssign):
        return [node.target.id] if isinstance(node.target, ast.Name) else []
    return [target.id for target in node.targets if isinstance(target, ast.Name)]


def _obsolete_factory_violations(
    relative_path: Path,
    tree: ast.Module,
) -> list[tuple[int, str]]:
    violations: list[tuple[int, str]] = []
    forbidden_definitions = FORBIDDEN_GLOBAL_DEFINITIONS.get(relative_path, set())
    for node in tree.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in forbidden_definitions
        ):
            violations.append((node.lineno, f"defines {node.name}"))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            violations.extend(
                (node.lineno, f"defines {target_name}")
                for target_name in _assignment_target_names(node)
                if target_name in forbidden_definitions
            )

    for module, imported_name, line_number in _imported_names(
        tree,
        relative_path=relative_path,
    ):
        if (module, imported_name) in FORBIDDEN_GLOBAL_IMPORTS:
            violations.append((line_number, f"imports {module}.{imported_name}"))

    forbidden_paths = {f"{module}.{name}" for module, name in FORBIDDEN_GLOBAL_IMPORTS}
    analyzer = _analyze_bindings(tree, relative_path=relative_path)
    violations.extend(
        (line_number, f"references {resolved}")
        for line_number, resolved in analyzer.attributes
        if resolved in forbidden_paths
    )
    return sorted(set(violations))


def test_obsolete_global_factories_cannot_be_defined_or_imported() -> None:
    violations: list[str] = []
    for relative_path, tree in _production_modules():
        violations.extend(
            f"{relative_path}:{line_number}: {message}"
            for line_number, message in _obsolete_factory_violations(
                relative_path,
                tree,
            )
        )

    assert violations == [], "\n".join(violations)


def test_get_settings_calls_stay_at_executable_boundaries() -> None:
    violations = [
        f"{relative_path}:{line_number}: calls get_settings"
        for relative_path, tree in _production_modules()
        if relative_path not in ALLOWED_SETTINGS_CALLERS
        for line_number in _settings_calls(tree, relative_path=relative_path)
    ]

    assert violations == [], "\n".join(violations)


def test_workers_do_not_import_the_api_application_or_routers() -> None:
    violations: list[str] = []
    for relative_path, tree in _production_modules():
        if not relative_path.is_relative_to(Path("app/workers")):
            continue
        for imported_path, line_number in _imported_module_paths(
            tree,
            relative_path=relative_path,
        ):
            if _is_forbidden_worker_import(imported_path):
                violations.append(f"{relative_path}:{line_number}: imports {imported_path}")

    assert violations == [], "\n".join(violations)


def test_business_modules_do_not_import_composition() -> None:
    business_roots = {
        Path("app/services"),
        Path("app/repositories"),
        Path("app/providers"),
        Path("app/models"),
    }
    violations: list[str] = []
    for relative_path, tree in _production_modules():
        if not any(relative_path.is_relative_to(root) for root in business_roots):
            continue
        for imported_path, line_number in _imported_module_paths(
            tree,
            relative_path=relative_path,
        ):
            if imported_path == "app.composition" or imported_path.startswith(
                "app.composition."
            ):
                violations.append(f"{relative_path}:{line_number}: imports {imported_path}")

    assert violations == [], "\n".join(violations)


def _is_forbidden_worker_import(imported_path: str) -> bool:
    return (
        imported_path == "app.main"
        or imported_path == "app.routers"
        or imported_path.startswith("app.routers.")
    )


class _CtxDependencyReadAnalyzer(ast.NodeVisitor):
    def __init__(self) -> None:
        self.ctx_names: set[str] = set()
        self.string_bindings: dict[str, str] = {}
        self.reads: list[tuple[int, str]] = []

    def _resolve_string(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return self.string_bindings.get(node.id)
        return None

    def _bind_target(self, target: ast.expr, value: ast.expr) -> None:
        if not isinstance(target, ast.Name):
            return
        if isinstance(value, ast.Name) and value.id in self.ctx_names:
            self.ctx_names.add(target.id)
        else:
            self.ctx_names.discard(target.id)
        resolved_string = self._resolve_string(value)
        if resolved_string is None:
            self.string_bindings.pop(target.id, None)
        else:
            self.string_bindings[target.id] = resolved_string

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self._bind_target(target, node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
            self._bind_target(node.target, node.value)

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in self.ctx_names
            and node.func.attr == "get"
            and node.args
        ):
            key = self._resolve_string(node.args[0])
            if key in APPLICATION_DEPENDENCY_KEYS:
                self.reads.append((node.lineno, key))
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if isinstance(node.value, ast.Name) and node.value.id in self.ctx_names:
            key = self._resolve_string(node.slice)
            if key in APPLICATION_DEPENDENCY_KEYS:
                self.reads.append((node.lineno, key))
        self.generic_visit(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        outer_ctx_names = self.ctx_names
        outer_string_bindings = self.string_bindings
        self.ctx_names = outer_ctx_names.copy()
        self.string_bindings = outer_string_bindings.copy()
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
            self.ctx_names.discard(argument.arg)
            self.string_bindings.pop(argument.arg, None)
            if argument.arg == "ctx":
                self.ctx_names.add(argument.arg)
        for statement in node.body:
            self.visit(statement)
        self.ctx_names = outer_ctx_names
        self.string_bindings = outer_string_bindings

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)


def _application_dependency_reads(tree: ast.Module) -> list[tuple[int, str]]:
    analyzer = _CtxDependencyReadAnalyzer()
    analyzer.visit(tree)
    return sorted(analyzer.reads)


def test_worker_jobs_do_not_read_application_dependencies_from_ctx() -> None:
    worker_logic_roots = {Path("app/workers/jobs"), Path("app/workers/outbox")}
    violations: list[str] = []
    for relative_path, tree in _production_modules():
        if not any(relative_path.is_relative_to(root) for root in worker_logic_roots):
            continue
        for line_number, key in _application_dependency_reads(tree):
            violations.append(
                f"{relative_path}:{line_number}: reads application dependency {key!r}"
            )

    assert violations == [], "\n".join(violations)


def test_relative_imports_are_resolved_before_dependency_direction_checks() -> None:
    tree = ast.parse("from ..routers import calls\nfrom .. import composition\n")

    imported_paths = _imported_module_paths(
        tree,
        relative_path=Path("app/workers/example.py"),
    )

    assert ("app.routers", 1) in imported_paths
    assert ("app.routers.calls", 1) in imported_paths
    assert ("app.composition", 2) in imported_paths


def test_settings_call_analysis_follows_only_relevant_import_bindings() -> None:
    cases = [
        (
            "from app.core.config import get_settings as load_settings\n"
            "again = load_settings\n"
            "again()\n",
            [3],
        ),
        (
            "import app.core.config as config\n"
            "load = config.get_settings\n"
            "load()\n",
            [3],
        ),
        (
            "import app.core.config\n"
            "app.core.config.get_settings()\n",
            [2],
        ),
        (
            "from app.core.config import *\n"
            "get_settings()\n",
            [2],
        ),
        (
            "from app.core.config import get_settings\n"
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
            relative_path=Path("app/services/example.py"),
        ) == expected_lines


def test_obsolete_factory_analysis_detects_attributes_and_compatibility_aliases() -> None:
    consumer_tree = ast.parse(
        "import app.core.database as db\n"
        "database = db\n"
        "factory = database.get_engine\n"
        "database.get_session_factory()\n"
    )
    definition_tree = ast.parse(
        "get_engine = get_session_factory = create_session_factory\n"
        "get_redis_client: object = create_redis_client\n"
    )
    local_name_tree = ast.parse(
        "def inspect_engine():\n"
        "    get_engine = object()\n"
        "    return get_engine\n"
    )

    assert _obsolete_factory_violations(
        Path("app/services/example.py"),
        consumer_tree,
    ) == [
        (3, "references app.core.database.get_engine"),
        (4, "references app.core.database.get_session_factory"),
    ]
    assert _obsolete_factory_violations(
        Path("app/core/database.py"),
        definition_tree,
    ) == [
        (1, "defines get_engine"),
        (1, "defines get_session_factory"),
    ]
    assert _obsolete_factory_violations(
        Path("app/core/database.py"),
        local_name_tree,
    ) == []


def test_worker_router_import_boundary_uses_a_complete_module_segment() -> None:
    assert _is_forbidden_worker_import("app.routers") is True
    assert _is_forbidden_worker_import("app.routers.calls") is True
    assert _is_forbidden_worker_import("app.routers_legacy") is False


def test_dependency_reads_follow_ctx_and_key_aliases() -> None:
    tree = ast.parse(
        'SESSION_KEY = "session_factory"\n'
        "async def job(ctx):\n"
        "    context = ctx\n"
        "    worker_context: dict = context\n"
        "    key = SESSION_KEY\n"
        "    alias = key\n"
        "    context.get(alias)\n"
        '    observation_key = "observability"\n'
        "    worker_context[observation_key]\n"
    )

    assert _application_dependency_reads(tree) == [
        (7, "session_factory"),
        (9, "observability"),
    ]


def test_dependency_reads_ignore_unrelated_mappings_and_module_names() -> None:
    tree = ast.parse(
        'payload.get("session_factory")\n'
        'snapshot["observability"]\n'
        'ctx.get("outbox_handlers")\n'
        "def job(payload):\n"
        '    return payload.get("telephony_provider")\n'
    )

    assert _application_dependency_reads(tree) == []
