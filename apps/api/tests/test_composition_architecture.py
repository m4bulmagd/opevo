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

SETTINGS_MODULE = "app.core.config"
SETTINGS_IMPORT_ANCESTORS = {"app", "app.core", SETTINGS_MODULE}


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
        self.passthrough_names: set[str] = set()

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

    def visit_Global(self, node: ast.Global) -> None:
        self.passthrough_names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.passthrough_names.update(node.names)


def _module_bindings(tree: ast.Module) -> list[tuple[int, str]]:
    collector = _ModuleBindingCollector()
    collector.visit(tree)
    return collector.bindings


def _import_bindings(
    tree: ast.Module,
    *,
    relative_path: Path,
) -> dict[str, set[str]]:
    bindings: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                local_name = imported.asname or imported.name.split(".", maxsplit=1)[0]
                resolved_name = imported.name if imported.asname else local_name
                bindings.setdefault(local_name, set()).add(resolved_name)
        elif isinstance(node, ast.ImportFrom):
            module = _resolved_from_module(node, relative_path)
            if module is None:
                continue
            for imported in node.names:
                if imported.name == "*":
                    continue
                local_name = imported.asname or imported.name
                bindings.setdefault(local_name, set()).add(
                    f"{module}.{imported.name}"
                )
    return bindings


def _resolved_import_expressions(
    node: ast.expr,
    *,
    bindings: dict[str, set[str]],
) -> set[str]:
    if isinstance(node, ast.Name):
        return bindings.get(node.id, set())
    if isinstance(node, ast.Attribute):
        return {
            f"{base}.{node.attr}"
            for base in _resolved_import_expressions(node.value, bindings=bindings)
        }
    return set()


def _literal_import_name(node: ast.Call) -> str | None:
    imported_name: ast.expr | None = node.args[0] if node.args else None
    if imported_name is None:
        imported_name = next(
            (keyword.value for keyword in node.keywords if keyword.arg == "name"),
            None,
        )
    if isinstance(imported_name, ast.Constant) and isinstance(imported_name.value, str):
        return imported_name.value
    return None


def _settings_boundary_violations(
    tree: ast.Module,
    *,
    relative_path: Path,
) -> list[tuple[int, str]]:
    violations: dict[int, str] = {}
    import_bindings = _import_bindings(tree, relative_path=relative_path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(imported.name == SETTINGS_MODULE for imported in node.names):
                violations[node.lineno] = f"imports {SETTINGS_MODULE} module"
        elif isinstance(node, ast.ImportFrom):
            module = _resolved_from_module(node, relative_path)
            if module is not None and any(
                f"{module}.{imported.name}" == SETTINGS_MODULE
                for imported in node.names
            ):
                violations[node.lineno] = f"imports {SETTINGS_MODULE} module"
                continue
            if module != SETTINGS_MODULE:
                continue
            if any(imported.name == "*" for imported in node.names):
                violations[node.lineno] = f"star-imports {SETTINGS_MODULE}"
            elif any(imported.name == "get_settings" for imported in node.names):
                violations[node.lineno] = f"imports {SETTINGS_MODULE}.get_settings"
        elif isinstance(node, ast.Attribute):
            if (
                f"{SETTINGS_MODULE}.get_settings"
                in _resolved_import_expressions(node, bindings=import_bindings)
            ):
                violations[node.lineno] = (
                    f"accesses {SETTINGS_MODULE}.get_settings"
                )
        elif isinstance(node, ast.Call):
            is_importlib_call = "importlib.import_module" in (
                _resolved_import_expressions(node.func, bindings=import_bindings)
            )
            is_builtin_import = (
                isinstance(node.func, ast.Name) and node.func.id == "__import__"
            )
            imported_name = _literal_import_name(node)
            if (
                (is_importlib_call or is_builtin_import)
                and imported_name in SETTINGS_IMPORT_ANCESTORS
            ):
                violations[node.lineno] = f"dynamically imports {imported_name}"

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


def _function_arguments(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
) -> list[ast.arg]:
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


def _callable_binds_ctx(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda) -> bool:
    if any(argument.arg == "ctx" for argument in _function_arguments(node)):
        return True
    collector = _ModuleBindingCollector()
    if isinstance(node, ast.Lambda):
        collector.visit(node.body)
    else:
        for statement in node.body:
            collector.visit(statement)
    return any(
        name == "ctx" and name not in collector.passthrough_names
        for _line, name in collector.bindings
    )


def _target_binds_ctx(target: ast.AST) -> bool:
    return any(
        isinstance(child, ast.Name)
        and child.id == "ctx"
        and isinstance(child.ctx, ast.Store)
        for child in ast.walk(target)
    )


def _class_statement_binds_ctx(statement: ast.stmt) -> bool:
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return statement.name == "ctx"
    if isinstance(statement, ast.Import):
        return any(
            (imported.asname or imported.name.split(".", maxsplit=1)[0]) == "ctx"
            for imported in statement.names
        )
    if isinstance(statement, ast.ImportFrom):
        return any(
            imported.name != "*" and (imported.asname or imported.name) == "ctx"
            for imported in statement.names
        )
    if isinstance(statement, ast.Assign):
        return any(_target_binds_ctx(target) for target in statement.targets)
    if isinstance(statement, ast.AnnAssign):
        return statement.value is not None and _target_binds_ctx(statement.target)
    if isinstance(statement, ast.AugAssign):
        return _target_binds_ctx(statement.target)
    return False


class _FunctionCtxUseVisitor(ast.NodeVisitor):
    def __init__(
        self,
        *,
        allowed_accessor: str | None,
        ctx_visible: bool = True,
        lexical_ctx_visible: bool | None = None,
        class_scope: bool = False,
    ) -> None:
        self.allowed_accessor = allowed_accessor
        self.ctx_visible = ctx_visible
        self.lexical_ctx_visible = (
            ctx_visible if lexical_ctx_visible is None else lexical_ctx_visible
        )
        self.class_scope = class_scope
        self.parents: list[ast.AST] = []
        self.violations: dict[int, str] = {}

    def _reject_binding(self, line: int, name: str | None) -> None:
        if self.ctx_visible and not self.class_scope and name == "ctx":
            self.violations[line] = "rebinds ctx"

    def _visit_child_scope(
        self,
        nodes: list[ast.AST],
        *,
        ctx_visible: bool,
        class_scope: bool = False,
    ) -> None:
        visitor = _FunctionCtxUseVisitor(
            allowed_accessor=self.allowed_accessor,
            ctx_visible=ctx_visible,
            lexical_ctx_visible=ctx_visible,
            class_scope=class_scope,
        )
        for node in nodes:
            visitor.visit(node)
            if class_scope and isinstance(node, ast.stmt):
                if _class_statement_binds_ctx(node):
                    visitor.ctx_visible = False
        self.violations.update(visitor.violations)

    def _visit_function_definition_expressions(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                self.visit(default)
        for argument in _function_arguments(node):
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if node.returns is not None:
            self.visit(node.returns)
        for type_parameter in node.type_params:
            self.visit(type_parameter)

    def visit(self, node: ast.AST) -> None:
        self.parents.append(node)
        super().visit(node)
        self.parents.pop()

    def visit_Name(self, node: ast.Name) -> None:
        if node.id != "ctx" or not self.ctx_visible:
            return
        if self.class_scope and isinstance(node.ctx, (ast.Store, ast.Del)):
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
        self._visit_function_definition_expressions(node)
        body_ctx_visible = (
            self.lexical_ctx_visible if self.class_scope else self.ctx_visible
        ) and not _callable_binds_ctx(node)
        self._visit_child_scope(node.body, ctx_visible=body_ctx_visible)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._reject_binding(node.lineno, node.name)
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        for type_parameter in node.type_params:
            self.visit(type_parameter)
        body_ctx_visible = (
            self.lexical_ctx_visible if self.class_scope else self.ctx_visible
        )
        self._visit_child_scope(
            node.body,
            ctx_visible=body_ctx_visible,
            class_scope=True,
        )

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
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                self.visit(default)
        body_ctx_visible = (
            self.lexical_ctx_visible if self.class_scope else self.ctx_visible
        ) and not _callable_binds_ctx(node)
        self._visit_child_scope([node.body], ctx_visible=body_ctx_visible)

    def _visit_comprehension(
        self,
        node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
    ) -> None:
        first_generator, *remaining_generators = node.generators
        self.visit(first_generator.iter)
        body_ctx_visible = (
            self.lexical_ctx_visible if self.class_scope else self.ctx_visible
        )
        visitor = _FunctionCtxUseVisitor(
            allowed_accessor=self.allowed_accessor,
            ctx_visible=body_ctx_visible,
            lexical_ctx_visible=body_ctx_visible,
        )
        generators = [first_generator, *remaining_generators]
        for index, generator in enumerate(generators):
            if index > 0:
                visitor.visit(generator.iter)
            if _target_binds_ctx(generator.target):
                visitor.ctx_visible = False
            for condition in generator.ifs:
                visitor.visit(condition)
        if isinstance(node, ast.DictComp):
            visitor.visit(node.key)
            visitor.visit(node.value)
        else:
            visitor.visit(node.elt)
        self.violations.update(visitor.violations)

    visit_ListComp = _visit_comprehension
    visit_SetComp = _visit_comprehension
    visit_DictComp = _visit_comprehension
    visit_GeneratorExp = _visit_comprehension


class _ModuleFunctionCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.functions: list[ast.FunctionDef | ast.AsyncFunctionDef] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.functions.append(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return


def _worker_ctx_violations(
    tree: ast.Module,
    *,
    allowed_accessor: str | None,
) -> list[tuple[int, str]]:
    violations: dict[int, str] = {}
    collector = _ModuleFunctionCollector()
    collector.visit(tree)
    for node in collector.functions:
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


def test_settings_boundary_resolves_direct_import_bindings_and_literal_imports() -> None:
    forbidden_cases = [
        ("import app\napp.core.config.get_settings()\n", [2]),
        ("import app as package\npackage.core.config.get_settings\n", [2]),
        ("import app.core as core\ncore.config.get_settings()\n", [2]),
        ("from app import core\ncore.config.get_settings()\n", [2]),
        (
            "from app import core as foundation\n"
            "foundation.config.get_settings()\n",
            [2],
        ),
        (
            "import importlib\n"
            'importlib.import_module("app")\n',
            [2],
        ),
        (
            "import importlib as loader\n"
            'loader.import_module("app.core")\n',
            [2],
        ),
        (
            "from importlib import import_module\n"
            'import_module("app.core.config")\n',
            [2],
        ),
        (
            "from importlib import import_module as load\n"
            'load("app")\n',
            [2],
        ),
        (
            "import importlib\n"
            'importlib.import_module(name="app.core.config")\n',
            [2],
        ),
        ('__import__("app.core")\n', [1]),
    ]

    for source, expected_lines in forbidden_cases:
        assert [
            line
            for line, _message in _settings_boundary_violations(
                ast.parse(source),
                relative_path=Path("app/services/example.py"),
            )
        ] == expected_lines


def test_settings_boundary_allows_unrelated_and_nonliteral_dynamic_imports() -> None:
    tree = ast.parse(
        "import app\n"
        "app.services.example\n"
        "import importlib as loader\n"
        'loader.import_module("livekit.plugins.turn_detector.multilingual")\n'
        'loader.import_module(f"livekit.plugins.{provider}")\n'
        "from importlib import import_module as load\n"
        "load(module_name)\n"
        '__import__("livekit.plugins")\n'
    )

    assert _settings_boundary_violations(
        tree,
        relative_path=Path("app/services/example.py"),
    ) == []


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


def test_worker_ctx_boundary_follows_module_compounds_without_rescanning_nested_defs() -> (
    None
):
    tree = ast.parse(
        "if enabled:\n"
        "    async def job(ctx):\n"
        "        return consume(ctx)\n"
        "async def outer(payload):\n"
        "    async def nested(ctx):\n"
        "        return consume(ctx)\n"
    )

    assert [
        line
        for line, _message in _worker_ctx_violations(
            tree,
            allowed_accessor="require_background_runtime",
        )
    ] == [3]

def test_worker_ctx_boundary_checks_nested_definition_time_expressions() -> None:
    forbidden_cases = [
        (
            "async def job(ctx):\n"
            "    def inner(ctx=consume(ctx)):\n"
            "        return ctx\n",
            [2],
        ),
        (
            "async def job(ctx):\n"
            "    @decorate(ctx)\n"
            "    def inner(ctx):\n"
            "        return consume(ctx)\n",
            [2],
        ),
        (
            "async def job(ctx):\n"
            "    callback = lambda ctx=consume(ctx): consume(ctx)\n",
            [2],
        ),
        (
            "async def job(ctx):\n"
            "    def inner(\n"
            "        value: annotate(ctx),\n"
            "        *,\n"
            "        keyword=consume(ctx),\n"
            "    ) -> returns(ctx):\n"
            "        return keyword\n",
            [3, 5, 6],
        ),
    ]

    for source, expected_lines in forbidden_cases:
        assert [
            line
            for line, _message in _worker_ctx_violations(
                ast.parse(source),
                allowed_accessor="require_background_runtime",
            )
        ] == expected_lines


def test_worker_ctx_boundary_honors_nested_callable_local_ctx_bindings() -> None:
    allowed_cases = [
        (
            "async def job(ctx):\n"
            "    def inner(ctx):\n"
            "        return consume(ctx)\n"
        ),
        (
            "async def job(ctx):\n"
            "    def inner():\n"
            "        ctx = local\n"
            "        return consume(ctx)\n"
        ),
        (
            "async def job(ctx):\n"
            "    callback = lambda ctx: consume(ctx)\n"
        ),
    ]

    for source in allowed_cases:
        assert (
            _worker_ctx_violations(
                ast.parse(source),
                allowed_accessor="require_background_runtime",
            )
            == []
        )


def test_worker_ctx_boundary_models_class_headers_and_sequential_namespace() -> None:
    forbidden_cases = [
        (
            "async def job(ctx):\n"
            "    class Holder:\n"
            "        leaked = consume(ctx)\n",
            [3],
        ),
        (
            "async def job(ctx):\n"
            "    class Holder(consume(ctx)):\n"
            "        pass\n",
            [2],
        ),
        (
            "async def job(ctx):\n"
            "    @decorate(ctx)\n"
            "    class Holder:\n"
            "        pass\n",
            [2],
        ),
        (
            "async def job(ctx, items):\n"
            "    class Holder:\n"
            "        ctx = local\n"
            "        def method(self):\n"
            "            return consume(ctx)\n"
            "        callback = lambda: consume(ctx)\n"
            "        values = [consume(ctx) for item in items]\n",
            [5, 6, 7],
        ),
    ]

    allowed = ast.parse(
        "async def job(ctx, items):\n"
        "    class Holder:\n"
        "        ctx = local\n"
        "        value = consume(ctx)\n"
        "        values = [consume(ctx) for ctx in items]\n"
    )

    for source, expected_lines in forbidden_cases:
        assert [
            line
            for line, _message in _worker_ctx_violations(
                ast.parse(source),
                allowed_accessor="require_background_runtime",
            )
        ] == expected_lines
    assert (
        _worker_ctx_violations(
            allowed,
            allowed_accessor="require_background_runtime",
        )
        == []
    )
