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


def _dotted_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        if parent is not None:
            return f"{parent}.{node.attr}"
    return None


def _settings_calls(tree: ast.Module, *, relative_path: Path) -> list[int]:
    local_names = {
        imported_name.asname or imported_name.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and _resolved_from_module(node, relative_path) == "app.core.config"
        for imported_name in node.names
        if imported_name.name == "get_settings"
    }
    module_names = {
        imported_name.asname or imported_name.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for imported_name in node.names
        if imported_name.name == "app.core.config"
    }
    module_names.update(
        imported_name.asname or imported_name.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and _resolved_from_module(node, relative_path) == "app.core"
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


def test_obsolete_global_factories_cannot_be_defined_or_imported() -> None:
    violations: list[str] = []
    for relative_path, tree in _production_modules():
        forbidden_definitions = FORBIDDEN_GLOBAL_DEFINITIONS.get(relative_path, set())
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in forbidden_definitions
            ):
                violations.append(f"{relative_path}:{node.lineno}: defines {node.name}")
        for module, imported_name, line_number in _imported_names(
            tree,
            relative_path=relative_path,
        ):
            if (module, imported_name) in FORBIDDEN_GLOBAL_IMPORTS:
                violations.append(
                    f"{relative_path}:{line_number}: imports {module}.{imported_name}"
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
            if imported_path == "app.main" or imported_path.startswith("app.routers"):
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


def _application_dependency_reads(tree: ast.Module) -> list[tuple[int, str]]:
    reads: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        key: object | None = None
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            key = node.args[0].value
        elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            key = node.slice.value
        if isinstance(key, str) and key in APPLICATION_DEPENDENCY_KEYS:
            reads.append((node.lineno, key))
    return sorted(reads)


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


def test_dependency_reads_detect_alternate_context_names_and_subscripts() -> None:
    tree = ast.parse(
        'context.get("session_factory")\nworker_context["observability"]\n'
    )

    assert _application_dependency_reads(tree) == [
        (1, "session_factory"),
        (2, "observability"),
    ]
