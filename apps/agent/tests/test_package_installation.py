"""Installation boundary checks for the shared wire-contract package."""

from importlib import metadata
from importlib import resources

import presvo_contracts


def test_shared_contract_package_is_installed() -> None:
    """Fail when the agent environment omits the declared shared distribution."""
    assert metadata.version("presvo-contracts") == "0.1.0"
    assert presvo_contracts.CURRENT_SCHEMA_VERSION == 1


def test_shared_contract_package_exposes_pep_561_marker() -> None:
    assert resources.files(presvo_contracts).joinpath("py.typed").is_file()
