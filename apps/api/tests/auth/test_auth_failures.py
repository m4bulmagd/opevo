import pytest

from app.core.auth_failures import AuthenticationUnavailable, TokenRejected


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (TokenRejected("authorized_party"), "token rejected"),
        (AuthenticationUnavailable("jwks_timeout"), "authentication unavailable"),
    ],
)
def test_auth_failures_expose_only_fixed_message_and_bounded_reason(
    failure: Exception, message: str
) -> None:
    assert str(failure) == message
    assert "SENSITIVE_PROVIDER_TEXT" not in repr(failure)


def test_auth_failure_retains_bounded_reason() -> None:
    assert TokenRejected("claims").reason == "claims"
