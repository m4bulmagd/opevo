from pathlib import Path
import subprocess
import sys


API_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TABLES = [
    "account_deactivation_operations",
    "activation_events",
    "agent_configs",
    "billing_checkout_attempts",
    "business_profiles",
    "call_messages",
    "calls",
    "customer_activations",
    "notifications",
    "outbox_events",
    "phone_number_provisionings",
    "phone_numbers",
    "provider_cleanup_operations",
    "recording_egress_operations",
    "subscription_cycle_history",
    "subscriptions",
    "usage_ledgers",
    "users",
    "webhook_events",
]


def test_models_package_registers_complete_alembic_metadata() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from app.models import Base; "
                "print('\\n'.join(sorted(Base.metadata.tables)))"
            ),
        ],
        cwd=API_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == EXPECTED_TABLES
