import ast
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = API_ROOT / "app"

ALLOWED_SETTINGS_MODULES = {
    Path("app/main.py"),
    Path("app/workers/arq_worker.py"),
    Path("app/core/config.py"),
}

FORBIDDEN_FACTORY_IMPORTS = {
    ("app.core.database", "get_engine"),
    ("app.core.database", "get_session_factory"),
    ("app.core.redis", "get_redis_client"),
    ("app.core.observability", "get_observability"),
    ("app.providers.storage.s3", "get_s3_storage"),
    ("app.providers.telephony.telnyx", "get_telephony_provider"),
}

RESERVED_FACTORY_NAMES = {name for _module, name in FORBIDDEN_FACTORY_IMPORTS}
FORBIDDEN_FACTORY_MODULES = {module for module, _name in FORBIDDEN_FACTORY_IMPORTS}

CALL_LIFECYCLE_CTX_MODULES = {
    Path("app/workers/jobs/call_finalization.py"),
    Path("app/workers/jobs/call_reconciliation.py"),
}

BACKGROUND_CTX_MODULES = {
    Path("app/workers/jobs/verification_expiry.py"),
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
    """Collect bindings executed in module scope without entering nested scopes."""

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
            if any(imported.name == "app.core.config" for imported in node.names):
                violations[node.lineno] = "imports app.core.config module"
        elif isinstance(node, ast.ImportFrom):
            module = _resolved_from_module(node, relative_path)
            if module is not None and any(
                f"{module}.{imported.name}" == "app.core.config"
                for imported in node.names
            ):
                violations[node.lineno] = "imports app.core.config module"
                continue
            if module != "app.core.config":
                continue
            if any(imported.name == "*" for imported in node.names):
                violations[node.lineno] = "star-imports app.core.config"
            elif any(imported.name == "get_settings" for imported in node.names):
                violations[node.lineno] = "imports app.core.config.get_settings"

    for line, name in _module_bindings(tree):
        if name == "get_settings" and line not in violations:
            violations[line] = "defines get_settings"
    return sorted(violations.items())


def _forbidden_factory_module_import(
    node: ast.Import | ast.ImportFrom,
    *,
    relative_path: Path,
) -> str | None:
    if isinstance(node, ast.Import):
        return next(
            (
                imported.name
                for imported in node.names
                if imported.name in FORBIDDEN_FACTORY_MODULES
            ),
            None,
        )
    module = _resolved_from_module(node, relative_path)
    if module is None:
        return None
    return next(
        (
            full_name
            for imported in node.names
            if (full_name := f"{module}.{imported.name}") in FORBIDDEN_FACTORY_MODULES
        ),
        None,
    )


def _obsolete_factory_violations(
    relative_path: Path,
    tree: ast.Module,
) -> list[tuple[int, str]]:
    violations = {
        (line, f"defines {name}")
        for line, name in _module_bindings(tree)
        if name in RESERVED_FACTORY_NAMES
    }
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        forbidden_module = _forbidden_factory_module_import(
            node,
            relative_path=relative_path,
        )
        if forbidden_module is not None:
            violations.add((node.lineno, f"imports {forbidden_module} module"))
        if not isinstance(node, ast.ImportFrom):
            continue
        module = _resolved_from_module(node, relative_path)
        if module is None:
            continue
        violations.update(
            (node.lineno, f"imports {module}.{imported.name}")
            for imported in node.names
            if (module, imported.name) in FORBIDDEN_FACTORY_IMPORTS
        )
    return sorted(violations)


def _function_arguments(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.arg]:
    arguments = [
        *node.args.posonlyargs,
        *node.args.args,
        *node.args.kwonlyargs,
    ]
    if node.args.vararg is not None:
        arguments.append(node.args.vararg)
    if node.args.kwarg is not None:
        arguments.append(node.args.kwarg)
    return arguments


class _FunctionCtxUseVisitor(ast.NodeVisitor):
    def __init__(self, *, allowed_accessor: str | None) -> None:
        self.allowed_accessor = allowed_accessor
        self.parents: list[ast.AST] = []
        self.violations: dict[int, str] = {}

    def _reject_binding(self, line: int, name: str | None) -> None:
        if name == "ctx":
            self.violations[line] = "rebinds ctx"

    def visit(self, node: ast.AST) -> None:
        self.parents.append(node)
        super().visit(node)
        self.parents.pop()

    def visit_Name(self, node: ast.Name) -> None:
        if node.id != "ctx":
            return
        parent = self.parents[-2] if len(self.parents) > 1 else None
        if (
            isinstance(node.ctx, ast.Load)
            and isinstance(parent, ast.Call)
            and self.allowed_accessor is not None
            and isinstance(parent.func, ast.Name)
            and parent.func.id == self.allowed_accessor
            and parent.args == [node]
            and not parent.keywords
        ):
            return
        self.violations[node.lineno] = (
            f"uses ctx outside {self.allowed_accessor}(ctx)"
            if self.allowed_accessor is not None
            else "uses ctx without an approved runtime accessor"
        )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._reject_binding(node.lineno, node.name)
        if any(argument.arg == "ctx" for argument in _function_arguments(node)):
            return
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._reject_binding(node.lineno, node.name)
        return

    def visit_Import(self, node: ast.Import) -> None:
        for imported in node.names:
            self._reject_binding(
                node.lineno,
                imported.asname or imported.name.split(".", maxsplit=1)[0],
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for imported in node.names:
            if imported.name != "*":
                self._reject_binding(
                    node.lineno,
                    imported.asname or imported.name,
                )

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self._reject_binding(node.lineno, node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        self._reject_binding(node.lineno, node.name)
        self.generic_visit(node)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        self._reject_binding(node.lineno, node.rest)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        self._reject_binding(node.lineno, node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        if any(argument.arg == "ctx" for argument in _function_arguments(node)):
            return
        self.generic_visit(node)


def _worker_ctx_violations(
    tree: ast.Module,
    *,
    allowed_accessor: str | None,
) -> list[tuple[int, str]]:
    violations: dict[int, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(argument.arg == "ctx" for argument in _function_arguments(node)):
            continue
        visitor = _FunctionCtxUseVisitor(allowed_accessor=allowed_accessor)
        for statement in node.body:
            visitor.visit(statement)
        violations.update(visitor.violations)
    return sorted(violations.items())


def _worker_ctx_accessor(relative_path: Path) -> str | None:
    if relative_path in CALL_LIFECYCLE_CTX_MODULES:
        return "require_call_lifecycle_runtime"
    if relative_path in BACKGROUND_CTX_MODULES or relative_path.is_relative_to(
        Path("app/workers/outbox")
    ):
        return "require_background_runtime"
    return None


def _is_forbidden_worker_import(imported_path: str) -> bool:
    return (
        imported_path == "app.main"
        or imported_path == "app.routers"
        or imported_path.startswith("app.routers.")
    )


def test_obsolete_global_factories_cannot_be_defined_or_imported() -> None:
    violations = [
        f"{relative_path}:{line}: {message}"
        for relative_path, tree in _production_modules()
        for line, message in _obsolete_factory_violations(relative_path, tree)
    ]

    assert violations == [], "\n".join(violations)


def test_settings_access_stays_at_executable_boundaries() -> None:
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


def test_workers_do_not_import_the_api_application_or_routers() -> None:
    violations: list[str] = []
    for relative_path, tree in _production_modules():
        if not relative_path.is_relative_to(Path("app/workers")):
            continue
        for imported_path, line in _imported_module_paths(
            tree,
            relative_path=relative_path,
        ):
            if _is_forbidden_worker_import(imported_path):
                violations.append(f"{relative_path}:{line}: imports {imported_path}")

    assert violations == [], "\n".join(violations)


def test_business_modules_do_not_import_composition() -> None:
    business_roots = {
        Path("app/services"),
        Path("app/repositories"),
        Path("app/providers"),
        Path("app/models"),
    }
    violations = [
        f"{relative_path}:{line}: imports {imported_path}"
        for relative_path, tree in _production_modules()
        if any(relative_path.is_relative_to(root) for root in business_roots)
        for imported_path, line in _imported_module_paths(
            tree,
            relative_path=relative_path,
        )
        if imported_path == "app.composition"
        or imported_path.startswith("app.composition.")
    ]

    assert violations == [], "\n".join(violations)


def test_worker_jobs_use_ctx_only_for_the_typed_runtime_accessor() -> None:
    worker_logic_roots = {Path("app/workers/jobs"), Path("app/workers/outbox")}
    violations = [
        f"{relative_path}:{line}: {message}"
        for relative_path, tree in _production_modules()
        if any(relative_path.is_relative_to(root) for root in worker_logic_roots)
        for line, message in _worker_ctx_violations(
            tree,
            allowed_accessor=_worker_ctx_accessor(relative_path),
        )
    ]

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


def test_settings_boundary_rejects_imports_and_module_scope_bindings() -> None:
    cases = [
        ("from app.core.config import get_settings\n", [1]),
        ("from app.core.config import get_settings as load\n", [1]),
        ("from app.core.config import *\n", [1]),
        ("import app.core.config\n", [1]),
        ("import app.core.config as config\n", [1]),
        ("from app.core import config as config_module\n", [1]),
        (
            "def load():\n"
            "    from app.core.config import get_settings as local\n"
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
                relative_path=Path("app/services/example.py"),
            )
        ] == expected_lines


def test_settings_boundary_allows_typed_imports_and_nested_shadows() -> None:
    tree = ast.parse(
        "from app.core.config import Settings\n"
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
            relative_path=Path("app/services/example.py"),
        )
        == []
    )


def test_obsolete_factory_imports_block_alias_and_module_escape() -> None:
    cases = [
        (
            "from app.core.database import get_engine as engine_factory\n",
            [(1, "imports app.core.database.get_engine")],
        ),
        (
            "import app.core.database as database\nfactory = database.get_engine\n",
            [(1, "imports app.core.database module")],
        ),
        (
            "from app.core import database as database_module\n",
            [(1, "imports app.core.database module")],
        ),
    ]

    for source, expected in cases:
        assert (
            _obsolete_factory_violations(
                Path("app/services/example.py"),
                ast.parse(source),
            )
            == expected
        )


def test_reserved_factory_names_apply_in_every_api_module() -> None:
    tree = ast.parse(
        "get_engine = create_database_engine\n"
        "get_session_factory: object = create_session_factory\n"
        "def get_redis_client():\n"
        "    return None\n"
        "class get_s3_storage:\n"
        "    pass\n"
        "import compatibility as get_telephony_provider\n"
        "from compatibility import observe as get_observability\n"
    )

    assert _obsolete_factory_violations(
        Path("app/services/compatibility.py"),
        tree,
    ) == [
        (1, "defines get_engine"),
        (2, "defines get_session_factory"),
        (3, "defines get_redis_client"),
        (5, "defines get_s3_storage"),
        (7, "defines get_telephony_provider"),
        (8, "defines get_observability"),
    ]


def test_reserved_factory_bindings_recurse_only_through_module_statements() -> None:
    tree = ast.parse(
        "if enabled:\n"
        "    get_engine, *rest = factories\n"
        "for get_session_factory in factories:\n"
        "    pass\n"
        "with resource() as get_redis_client:\n"
        "    pass\n"
        "try:\n"
        "    pass\n"
        "except Error as get_s3_storage:\n"
        "    pass\n"
        "match value:\n"
        '    case {"provider": get_telephony_provider}:\n'
        "        pass\n"
        "while (get_observability := next_value()):\n"
        "    break\n"
        "def local_scope():\n"
        "    get_engine = local\n"
        "class Holder:\n"
        "    get_session_factory = local\n"
    )

    assert _obsolete_factory_violations(
        Path("app/services/example.py"),
        tree,
    ) == [
        (2, "defines get_engine"),
        (3, "defines get_session_factory"),
        (5, "defines get_redis_client"),
        (9, "defines get_s3_storage"),
        (12, "defines get_telephony_provider"),
        (14, "defines get_observability"),
    ]


def test_reserved_factory_bindings_include_compound_defs_classes_and_imports() -> None:
    tree = ast.parse(
        "if enabled:\n"
        "    def get_engine():\n"
        "        return None\n"
        "else:\n"
        "    class get_s3_storage:\n"
        "        pass\n"
        "try:\n"
        "    import compatibility as get_redis_client\n"
        "except Exception:\n"
        "    from compatibility import observe as get_observability\n"
    )

    assert _obsolete_factory_violations(
        Path("app/services/example.py"),
        tree,
    ) == [
        (2, "defines get_engine"),
        (5, "defines get_s3_storage"),
        (8, "defines get_redis_client"),
        (10, "defines get_observability"),
    ]


def test_worker_router_import_boundary_uses_a_complete_module_segment() -> None:
    assert _is_forbidden_worker_import("app.routers") is True
    assert _is_forbidden_worker_import("app.routers.calls") is True
    assert _is_forbidden_worker_import("app.routers_legacy") is False


def test_worker_ctx_boundary_allows_only_the_direct_typed_runtime_accessor() -> None:
    allowed = ast.parse(
        "async def job(ctx, payload):\n"
        "    runtime = require_background_runtime(ctx)\n"
        '    return payload.get("session_factory"), runtime\n'
    )
    forbidden_cases = [
        (
            "async def job(ctx, enabled):\n"
            "    if enabled:\n"
            "        context = ctx\n"
            "    return context\n",
            [3],
        ),
        (
            "async def job(ctx):\n"
            "    try:\n"
            "        context = ctx\n"
            "    except Exception:\n"
            "        context = {}\n",
            [3],
        ),
        (
            "async def job(ctx):\n"
            "    try:\n"
            "        pass\n"
            "    except Exception as ctx:\n"
            "        pass\n",
            [4],
        ),
        (
            "async def job(ctx, items):\n"
            "    for item in items:\n"
            "        context = ctx\n",
            [3],
        ),
        ("async def job(ctx):\n    import payload as ctx\n", [2]),
        (
            "async def job(ctx, value):\n"
            "    match value:\n"
            "        case _ as ctx:\n"
            "            pass\n",
            [3],
        ),
        ("async def job(ctx):\n    ctx = {}\n", [2]),
        ("async def job(ctx):\n    return consume(ctx)\n", [2]),
        ("async def job(ctx):\n    return ctx.get('job_try')\n", [2]),
        (
            "async def job(ctx):\n    return require_call_lifecycle_runtime(ctx)\n",
            [2],
        ),
    ]

    assert (
        _worker_ctx_violations(
            allowed,
            allowed_accessor="require_background_runtime",
        )
        == []
    )
    for source, expected_lines in forbidden_cases:
        assert [
            line
            for line, _message in _worker_ctx_violations(
                ast.parse(source),
                allowed_accessor="require_background_runtime",
            )
        ] == expected_lines


def test_worker_ctx_boundary_ignores_functions_without_ctx_and_other_mappings() -> None:
    tree = ast.parse(
        "async def job(context, payload):\n"
        '    context.get("session_factory")\n'
        '    payload["observability"]\n'
    )

    assert (
        _worker_ctx_violations(
            tree,
            allowed_accessor="require_background_runtime",
        )
        == []
    )
