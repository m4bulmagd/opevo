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

RESERVED_FACTORY_NAMES = {
    "get_engine",
    "get_session_factory",
    "get_redis_client",
    "get_s3_storage",
    "get_telephony_provider",
    "get_observability",
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
            (module, imported_name.name, node.lineno) for imported_name in node.names
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
            imported.extend(
                (imported_name.name, node.lineno) for imported_name in node.names
            )
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


BindingState = dict[str, frozenset[str]]


def _merge_binding_states(*states: BindingState) -> BindingState:
    return {
        name: frozenset().union(*(state.get(name, frozenset()) for state in states))
        for name in set().union(*(state.keys() for state in states))
    }


class _LocalBindingCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()
        self.global_names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        self.names.update(
            imported.asname or imported.name.split(".", maxsplit=1)[0]
            for imported in node.names
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.names.update(
            imported.asname or imported.name
            for imported in node.names
            if imported.name != "*"
        )

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.names.add(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ListComp(self, node: ast.ListComp) -> None:
        return

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_Global(self, node: ast.Global) -> None:
        self.global_names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.global_names.update(node.names)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name is not None:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name is not None:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest is not None:
            self.names.add(node.rest)
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name is not None:
            self.names.add(node.name)


def _function_local_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    collector = _LocalBindingCollector()
    for statement in node.body:
        collector.visit(statement)
    arguments = [
        *node.args.posonlyargs,
        *node.args.args,
        *node.args.kwonlyargs,
    ]
    if node.args.vararg is not None:
        arguments.append(node.args.vararg)
    if node.args.kwarg is not None:
        arguments.append(node.args.kwarg)
    return (
        collector.names | {argument.arg for argument in arguments}
    ) - collector.global_names


def _pattern_binding_names(pattern: ast.pattern) -> set[str]:
    return {
        child.name
        for child in ast.walk(pattern)
        if isinstance(child, ast.MatchAs) and child.name is not None
    } | {
        child.rest
        for child in ast.walk(pattern)
        if isinstance(child, ast.MatchMapping) and child.rest is not None
    }


def _match_is_exhaustive(node: ast.Match) -> bool:
    if not node.cases:
        return False
    last_case = node.cases[-1]
    return (
        last_case.guard is None
        and isinstance(last_case.pattern, ast.MatchAs)
        and last_case.pattern.pattern is None
        and last_case.pattern.name is None
    )


class _BindingAnalyzer(ast.NodeVisitor):
    def __init__(self, *, relative_path: Path) -> None:
        self.relative_path = relative_path
        self.bindings: BindingState = {}
        self.calls: list[tuple[int, str]] = []
        self.attributes: list[tuple[int, str]] = []
        self.scope_kind = "module"
        self.class_lexical_bindings: BindingState | None = None

    def _resolve(self, node: ast.expr) -> frozenset[str]:
        if isinstance(node, ast.Name):
            return self.bindings.get(node.id, frozenset())
        if isinstance(node, ast.Attribute):
            return frozenset(
                f"{parent}.{node.attr}" for parent in self._resolve(node.value)
            )
        return frozenset()

    def _bind_target(
        self,
        target: ast.expr,
        provenances: frozenset[str] = frozenset(),
    ) -> None:
        if isinstance(target, ast.Name):
            self.bindings[target.id] = provenances
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._bind_target(element)

    def _analyze_branch(
        self,
        statements: list[ast.stmt],
        initial: BindingState,
    ) -> BindingState:
        saved = self.bindings
        self.bindings = initial.copy()
        for statement in statements:
            self.visit(statement)
        result = self.bindings
        self.bindings = saved
        return result

    def visit_Import(self, node: ast.Import) -> None:
        for imported_name in node.names:
            if imported_name.asname is not None:
                self.bindings[imported_name.asname] = frozenset({imported_name.name})
            else:
                root_name = imported_name.name.split(".", maxsplit=1)[0]
                self.bindings[root_name] = frozenset({root_name})

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = _resolved_from_module(node, self.relative_path)
        if module is None:
            return
        for imported_name in node.names:
            if imported_name.name == "*":
                if module == "app.core.config":
                    self.bindings["get_settings"] = frozenset(
                        {"app.core.config.get_settings"}
                    )
                continue
            local_name = imported_name.asname or imported_name.name
            self.bindings[local_name] = frozenset({f"{module}.{imported_name.name}"})

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self._bind_target(target, self._resolve(node.value))

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
            self._bind_target(node.target, self._resolve(node.value))
        else:
            self._bind_target(node.target)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.target)
        self.visit(node.value)
        self._bind_target(node.target)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._bind_target(node.target, self._resolve(node.value))

    def visit_Call(self, node: ast.Call) -> None:
        for resolved in self._resolve(node.func):
            self.calls.append((node.lineno, resolved))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        for resolved in self._resolve(node):
            self.attributes.append((node.lineno, resolved))
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        initial = self.bindings.copy()
        body = self._analyze_branch(node.body, initial)
        otherwise = (
            self._analyze_branch(node.orelse, initial) if node.orelse else initial
        )
        self.bindings = _merge_binding_states(body, otherwise)

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        initial = self.bindings.copy()
        saved = self.bindings
        self.bindings = initial.copy()
        self._bind_target(node.target)
        for statement in node.body:
            self.visit(statement)
        body = self.bindings
        self.bindings = saved
        merged = _merge_binding_states(initial, body)
        self.bindings = (
            self._analyze_branch(node.orelse, merged) if node.orelse else merged
        )

    visit_AsyncFor = visit_For

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        initial = self.bindings.copy()
        body = self._analyze_branch(node.body, initial)
        merged = _merge_binding_states(initial, body)
        self.bindings = (
            self._analyze_branch(node.orelse, merged) if node.orelse else merged
        )

    def visit_Try(self, node: ast.Try) -> None:
        initial = self.bindings.copy()
        body = self._analyze_branch(node.body, initial)
        normal = self._analyze_branch(node.orelse, body) if node.orelse else body
        outcomes = [normal]
        for handler in node.handlers:
            if handler.type is not None:
                self.visit(handler.type)
            handler_initial = initial.copy()
            if handler.name is not None:
                handler_initial[handler.name] = frozenset()
            outcomes.append(self._analyze_branch(handler.body, handler_initial))
        merged = _merge_binding_states(*outcomes)
        self.bindings = (
            self._analyze_branch(node.finalbody, merged) if node.finalbody else merged
        )

    visit_TryStar = visit_Try

    def visit_Match(self, node: ast.Match) -> None:
        self.visit(node.subject)
        initial = self.bindings.copy()
        outcomes = [] if _match_is_exhaustive(node) else [initial]
        for case in node.cases:
            saved = self.bindings
            self.bindings = initial.copy()
            for name in _pattern_binding_names(case.pattern):
                self.bindings[name] = frozenset()
            if case.guard is not None:
                self.visit(case.guard)
            for statement in case.body:
                self.visit(statement)
            outcomes.append(self.bindings)
            self.bindings = saved
        self.bindings = _merge_binding_states(*outcomes)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                self.visit(default)
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
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if node.returns is not None:
            self.visit(node.returns)

        outer_bindings = self.bindings
        outer_scope_kind = self.scope_kind
        outer_class_lexical_bindings = self.class_lexical_bindings
        lexical_bindings = (
            self.class_lexical_bindings
            if self.scope_kind == "class" and self.class_lexical_bindings is not None
            else outer_bindings
        )
        self.bindings = lexical_bindings.copy()
        if self.scope_kind != "class":
            self.bindings[node.name] = frozenset()
        for local_name in _function_local_names(node):
            self.bindings[local_name] = frozenset()
        self.scope_kind = "function"
        self.class_lexical_bindings = None
        for statement in node.body:
            self.visit(statement)
        self.bindings = outer_bindings
        self.scope_kind = outer_scope_kind
        self.class_lexical_bindings = outer_class_lexical_bindings
        self.bindings[node.name] = frozenset()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for expression in [*node.decorator_list, *node.bases]:
            self.visit(expression)
        for keyword in node.keywords:
            self.visit(keyword.value)

        outer_bindings = self.bindings
        outer_scope_kind = self.scope_kind
        outer_class_lexical_bindings = self.class_lexical_bindings
        lexical_bindings = (
            self.class_lexical_bindings
            if self.scope_kind == "class" and self.class_lexical_bindings is not None
            else outer_bindings
        )
        self.bindings = outer_bindings.copy()
        self.scope_kind = "class"
        self.class_lexical_bindings = lexical_bindings.copy()
        for statement in node.body:
            self.visit(statement)
        self.bindings = outer_bindings
        self.scope_kind = outer_scope_kind
        self.class_lexical_bindings = outer_class_lexical_bindings
        self.bindings[node.name] = frozenset()

    def visit_Lambda(self, node: ast.Lambda) -> None:
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
            self.bindings[argument.arg] = frozenset()
        self.visit(node.body)
        self.bindings = outer_bindings

    def _visit_comprehension(
        self,
        node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
    ) -> None:
        first, *remaining = node.generators
        self.visit(first.iter)
        outer_bindings = self.bindings
        self.bindings = outer_bindings.copy()
        self._bind_target(first.target)
        for condition in first.ifs:
            self.visit(condition)
        for generator in remaining:
            self.visit(generator.iter)
            self._bind_target(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)
        self.bindings = outer_bindings

    visit_ListComp = _visit_comprehension
    visit_SetComp = _visit_comprehension
    visit_DictComp = _visit_comprehension
    visit_GeneratorExp = _visit_comprehension


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
    def names(target: ast.expr) -> list[str]:
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, (ast.Tuple, ast.List)):
            return [name for element in target.elts for name in names(element)]
        return []

    targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
    return [name for target in targets for name in names(target)]


def _obsolete_factory_violations(
    relative_path: Path,
    tree: ast.Module,
) -> list[tuple[int, str]]:
    violations: list[tuple[int, str]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and (
            node.name in RESERVED_FACTORY_NAMES
        ):
            violations.append((node.lineno, f"defines {node.name}"))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            violations.extend(
                (node.lineno, f"defines {target_name}")
                for target_name in _assignment_target_names(node)
                if target_name in RESERVED_FACTORY_NAMES
            )
        elif isinstance(node, ast.Import):
            violations.extend(
                (node.lineno, f"defines {local_name}")
                for imported_name in node.names
                if (
                    local_name := imported_name.asname
                    or imported_name.name.split(".", maxsplit=1)[0]
                )
                in RESERVED_FACTORY_NAMES
            )
        elif isinstance(node, ast.ImportFrom):
            violations.extend(
                (node.lineno, f"defines {local_name}")
                for imported_name in node.names
                if imported_name.name != "*"
                and (local_name := imported_name.asname or imported_name.name)
                in RESERVED_FACTORY_NAMES
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
                violations.append(
                    f"{relative_path}:{line_number}: imports {imported_path}"
                )

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
                violations.append(
                    f"{relative_path}:{line_number}: imports {imported_path}"
                )

    assert violations == [], "\n".join(violations)


def _is_forbidden_worker_import(imported_path: str) -> bool:
    return (
        imported_path == "app.main"
        or imported_path == "app.routers"
        or imported_path.startswith("app.routers.")
    )


class _CtxDependencyReadAnalyzer(ast.NodeVisitor):
    def __init__(self) -> None:
        self.bindings: BindingState = {}
        self.reads: list[tuple[int, str]] = []

    def _resolve(self, node: ast.expr) -> frozenset[str]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return frozenset({f"key:{node.value}"})
        if isinstance(node, ast.Name):
            return self.bindings.get(node.id, frozenset())
        return frozenset()

    def _bind_target(
        self,
        target: ast.expr,
        provenances: frozenset[str] = frozenset(),
    ) -> None:
        if isinstance(target, ast.Name):
            self.bindings[target.id] = provenances
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._bind_target(element)

    def _analyze_branch(
        self,
        statements: list[ast.stmt],
        initial: BindingState,
    ) -> BindingState:
        saved = self.bindings
        self.bindings = initial.copy()
        for statement in statements:
            self.visit(statement)
        result = self.bindings
        self.bindings = saved
        return result

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self._bind_target(target, self._resolve(node.value))

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
            self._bind_target(node.target, self._resolve(node.value))
        else:
            self._bind_target(node.target)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.target)
        self.visit(node.value)
        self._bind_target(node.target)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._bind_target(node.target, self._resolve(node.value))

    def _record_read(self, node: ast.expr, key_node: ast.expr, line: int) -> None:
        if "ctx" not in self._resolve(node):
            return
        for provenance in self._resolve(key_node):
            if not provenance.startswith("key:"):
                continue
            key = provenance.removeprefix("key:")
            if key in APPLICATION_DEPENDENCY_KEYS:
                self.reads.append((line, key))

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
        ):
            self._record_read(node.func.value, node.args[0], node.lineno)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        self._record_read(node.value, node.slice, node.lineno)
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        self.visit(node.test)
        initial = self.bindings.copy()
        body = self._analyze_branch(node.body, initial)
        otherwise = (
            self._analyze_branch(node.orelse, initial) if node.orelse else initial
        )
        self.bindings = _merge_binding_states(body, otherwise)

    def visit_For(self, node: ast.For) -> None:
        self.visit(node.iter)
        initial = self.bindings.copy()
        saved = self.bindings
        self.bindings = initial.copy()
        self._bind_target(node.target)
        for statement in node.body:
            self.visit(statement)
        body = self.bindings
        self.bindings = saved
        merged = _merge_binding_states(initial, body)
        self.bindings = (
            self._analyze_branch(node.orelse, merged) if node.orelse else merged
        )

    visit_AsyncFor = visit_For

    def visit_While(self, node: ast.While) -> None:
        self.visit(node.test)
        initial = self.bindings.copy()
        body = self._analyze_branch(node.body, initial)
        merged = _merge_binding_states(initial, body)
        self.bindings = (
            self._analyze_branch(node.orelse, merged) if node.orelse else merged
        )

    def visit_Try(self, node: ast.Try) -> None:
        initial = self.bindings.copy()
        body = self._analyze_branch(node.body, initial)
        normal = self._analyze_branch(node.orelse, body) if node.orelse else body
        outcomes = [normal]
        for handler in node.handlers:
            if handler.type is not None:
                self.visit(handler.type)
            handler_initial = initial.copy()
            if handler.name is not None:
                handler_initial[handler.name] = frozenset()
            outcomes.append(self._analyze_branch(handler.body, handler_initial))
        merged = _merge_binding_states(*outcomes)
        self.bindings = (
            self._analyze_branch(node.finalbody, merged) if node.finalbody else merged
        )

    visit_TryStar = visit_Try

    def visit_Match(self, node: ast.Match) -> None:
        self.visit(node.subject)
        initial = self.bindings.copy()
        outcomes = [] if _match_is_exhaustive(node) else [initial]
        for case in node.cases:
            saved = self.bindings
            self.bindings = initial.copy()
            for name in _pattern_binding_names(case.pattern):
                self.bindings[name] = frozenset()
            if case.guard is not None:
                self.visit(case.guard)
            for statement in case.body:
                self.visit(statement)
            outcomes.append(self.bindings)
            self.bindings = saved
        self.bindings = _merge_binding_states(*outcomes)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        outer_bindings = self.bindings
        self.bindings = outer_bindings.copy()
        for local_name in _function_local_names(node):
            self.bindings[local_name] = frozenset()
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
            if argument.arg == "ctx":
                self.bindings[argument.arg] = frozenset({"ctx"})
        for statement in node.body:
            self.visit(statement)
        self.bindings = outer_bindings

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)


def _application_dependency_reads(tree: ast.Module) -> list[tuple[int, str]]:
    analyzer = _CtxDependencyReadAnalyzer()
    analyzer.visit(tree)
    return sorted(set(analyzer.reads))


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
            "import app.core.config as config\nload = config.get_settings\nload()\n",
            [3],
        ),
        (
            "import app.core.config\napp.core.config.get_settings()\n",
            [2],
        ),
        (
            "from app.core.config import *\nget_settings()\n",
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
            "def get_settings():\n    return None\nget_settings()\n",
            [],
        ),
    ]

    for source, expected_lines in cases:
        assert (
            _settings_calls(
                ast.parse(source),
                relative_path=Path("app/services/example.py"),
            )
            == expected_lines
        )


def test_settings_call_analysis_models_python_evaluation_scopes() -> None:
    cases = [
        (
            "from app.core.config import get_settings\n"
            "safe = lambda get_settings: get_settings()\n"
            "uses_outer = lambda value=get_settings(): value\n",
            [3],
        ),
        (
            "from app.core.config import get_settings\n"
            "[get_settings() for get_settings in factories]\n"
            "[item for item in get_settings()]\n"
            "[get_settings() for item in items if get_settings()]\n",
            [3, 4, 4],
        ),
        (
            "from app.core.config import get_settings\n"
            "@get_settings()\n"
            "def get_settings(\n"
            "    arg: get_settings() = get_settings(),\n"
            "    *,\n"
            "    keyword: get_settings() = get_settings(),\n"
            ") -> get_settings():\n"
            "    return arg\n",
            [2, 4, 4, 6, 6, 7],
        ),
        (
            "from app.core.config import get_settings\n"
            "@get_settings()\n"
            "class get_settings(get_settings(), metaclass=get_settings()):\n"
            "    value: get_settings()\n",
            [2, 3, 3, 4],
        ),
        (
            "from app.core.config import get_settings\n"
            "class RuntimeConsumer:\n"
            "    def get_settings(self):\n"
            "        return None\n"
            "    value = get_settings()\n",
            [],
        ),
        (
            "from app.core.config import get_settings\n"
            "def use_local():\n"
            "    def get_settings():\n"
            "        return None\n"
            "    return get_settings()\n",
            [],
        ),
    ]

    for source, expected_lines in cases:
        assert (
            _settings_calls(
                ast.parse(source),
                relative_path=Path("app/services/example.py"),
            )
            == expected_lines
        )


def test_settings_call_analysis_merges_possible_control_flow_bindings() -> None:
    cases = [
        (
            "from app.core.config import get_settings as load\n"
            "if enabled:\n"
            "    load = local\n"
            "load()\n",
            [4],
        ),
        (
            "from app.core.config import get_settings\n"
            "if enabled:\n"
            "    load = get_settings\n"
            "else:\n"
            "    load = local\n"
            "load()\n",
            [6],
        ),
        (
            "from app.core.config import get_settings\n"
            "load = get_settings\n"
            "try:\n"
            "    load = local\n"
            "except Exception:\n"
            "    pass\n"
            "load()\n",
            [7],
        ),
        (
            "from app.core.config import get_settings\n"
            "load = get_settings\n"
            "match value:\n"
            "    case 1:\n"
            "        load = local\n"
            "load()\n",
            [6],
        ),
        (
            "from app.core.config import get_settings\n"
            "load = get_settings\n"
            "for item in items:\n"
            "    load = local\n"
            "load()\n",
            [5],
        ),
        (
            "from app.core.config import get_settings\nload = local\nload()\n",
            [],
        ),
    ]

    for source, expected_lines in cases:
        assert (
            _settings_calls(
                ast.parse(source),
                relative_path=Path("app/services/example.py"),
            )
            == expected_lines
        )


def test_obsolete_factory_analysis_detects_attributes_and_compatibility_aliases() -> (
    None
):
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
        "def inspect_engine():\n    get_engine = object()\n    return get_engine\n"
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
        (2, "defines get_redis_client"),
    ]
    assert (
        _obsolete_factory_violations(
            Path("app/core/database.py"),
            local_name_tree,
        )
        == []
    )


def test_obsolete_factory_names_are_reserved_in_every_api_module() -> None:
    facade_tree = ast.parse(
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
        facade_tree,
    ) == [
        (1, "defines get_engine"),
        (2, "defines get_session_factory"),
        (3, "defines get_redis_client"),
        (5, "defines get_s3_storage"),
        (7, "defines get_telephony_provider"),
        (8, "defines get_observability"),
    ]


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


def test_dependency_reads_merge_possible_ctx_and_key_control_flow() -> None:
    tree = ast.parse(
        "async def job(ctx, payload, enabled, value, items):\n"
        "    context = ctx\n"
        '    key = "session_factory"\n'
        "    if enabled:\n"
        "        context = payload\n"
        '        key = "ordinary"\n'
        "    context.get(key)\n"
        "    try:\n"
        "        selected = ctx\n"
        "    except Exception:\n"
        "        selected = payload\n"
        '    selected["observability"]\n'
        "    match value:\n"
        "        case 1:\n"
        "            matched = ctx\n"
        "        case _:\n"
        "            matched = payload\n"
        '    matched.get("outbox_handlers")\n'
        "    loop_context = ctx\n"
        "    for item in items:\n"
        "        loop_context = payload\n"
        '    loop_context.get("telephony_provider")\n'
    )

    assert _application_dependency_reads(tree) == [
        (7, "session_factory"),
        (12, "observability"),
        (18, "outbox_handlers"),
        (22, "telephony_provider"),
    ]


def test_dependency_reads_allow_unconditional_ctx_and_key_reassignment() -> None:
    tree = ast.parse(
        "async def job(ctx, payload):\n"
        "    context = ctx\n"
        "    context = payload\n"
        '    context.get("session_factory")\n'
        '    key = "observability"\n'
        '    key = "ordinary"\n'
        "    ctx.get(key)\n"
        '    payload.get("outbox_handlers")\n'
    )

    assert _application_dependency_reads(tree) == []
