from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2] / "app"
EXTERNAL_IDENTITY_ALLOWED_MODULES = {
    Path("auth/domain.py"),
    Path("auth/providers/clerk.py"),
    Path("auth/providers/local.py"),
    Path("auth/providers/supabase.py"),
    Path("models/user.py"),
    Path("repositories/user_repository.py"),
    Path("services/authentication_service.py"),
    Path("services/user_provisioning.py"),
}


def test_legacy_clerk_identity_name_is_absent_from_application_code() -> None:
    offenders = [
        path.relative_to(APP_ROOT)
        for path in APP_ROOT.rglob("*.py")
        if "clerk_user_id" in path.read_text()
    ]

    assert offenders == []


def test_external_identity_is_contained_to_authentication_boundary() -> None:
    modules_using_external_identity = {
        path.relative_to(APP_ROOT)
        for path in APP_ROOT.rglob("*.py")
        if "external_user_id" in path.read_text()
    }

    assert modules_using_external_identity <= EXTERNAL_IDENTITY_ALLOWED_MODULES
