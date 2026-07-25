from pathlib import Path
import importlib.util

from app.models import Base
from app.models.billing_checkout_attempt import BillingCheckoutAttempt
from app.models.provider_cleanup_operation import ProviderCleanupOperation
from app.models.subscription_cycle_history import SubscriptionCycleHistory


REPO_ROOT = Path(__file__).resolve().parents[3]
REVISION = (
    REPO_ROOT
    / "apps/api/alembic/versions/0016_add_lifecycle_cleanup_and_billing_history.py"
)


def test_lifecycle_cleanup_models_have_durable_uniqueness_boundaries() -> None:
    checkout_constraints = {
        constraint.name for constraint in BillingCheckoutAttempt.__table__.constraints
    }
    cleanup_constraints = {
        constraint.name for constraint in ProviderCleanupOperation.__table__.constraints
    }
    history_constraints = {
        constraint.name for constraint in SubscriptionCycleHistory.__table__.constraints
    }

    assert {
        "uq_billing_checkout_attempts_user_generation",
        "uq_billing_checkout_attempts_idempotency_key",
        "ck_billing_checkout_attempts_status_allowed",
    } <= checkout_constraints
    assert {
        "uq_provider_cleanup_operations_resource",
        "ck_provider_cleanup_operations_resource_type_allowed",
        "ck_provider_cleanup_operations_status_allowed",
        "ck_provider_cleanup_operations_completion_consistent",
    } <= cleanup_constraints
    assert {
        "uq_subscription_cycle_history_user_generation",
        "uq_subscription_cycle_history_stripe_subscription_id",
    } <= history_constraints


def test_revision_declares_expected_parent_and_tables() -> None:
    spec = importlib.util.spec_from_file_location("lifecycle_cleanup_revision", REVISION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "0016_lifecycle_cleanup"
    assert module.down_revision == "0015_account_deactivation"


def test_metadata_registry_contains_all_lifecycle_cleanup_tables() -> None:
    table_names = set(Base.metadata.tables)
    assert {
        "billing_checkout_attempts",
        "provider_cleanup_operations",
        "subscription_cycle_history",
    } <= table_names
