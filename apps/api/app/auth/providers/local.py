import hmac

from app.auth.domain import ExternalIdentity, ExternalUserProfile
from app.auth.providers.base import AuthProvider
from app.core.auth_failures import TokenRejected


LOCAL_USER_EXTERNAL_ID = "local_opevo_user"
LOCAL_USER_EMAIL = "local@opevo.invalid"


class LocalAuthProvider(AuthProvider):
    def __init__(self, *, token: str) -> None:
        self._token = token.encode("utf-8")

    async def verify_token(self, token: str) -> ExternalIdentity:
        if not hmac.compare_digest(token.encode("utf-8"), self._token):
            raise TokenRejected("signature")
        profile = ExternalUserProfile(
            external_user_id=LOCAL_USER_EXTERNAL_ID,
            email=LOCAL_USER_EMAIL,
        )
        return ExternalIdentity(
            external_user_id=profile.external_user_id,
            bootstrap_profile=profile,
        )
