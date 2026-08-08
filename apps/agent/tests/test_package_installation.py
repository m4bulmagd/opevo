"""Installation boundary checks for the shared wire-contract package."""

from importlib import metadata
from importlib import resources

import opevo_contracts


def test_shared_contract_package_is_installed() -> None:
    """Fail when the agent environment omits the declared shared distribution."""
    assert metadata.version("opevo-contracts") == "0.1.0"
    assert opevo_contracts.CURRENT_SCHEMA_VERSION == 1


def test_shared_contract_package_exposes_pep_561_marker() -> None:
    assert resources.files(opevo_contracts).joinpath("py.typed").is_file()
