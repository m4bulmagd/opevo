from dataclasses import dataclass
from uuid import UUID


def _require_non_empty(value: str, *, name: str) -> None:
    if not value:
        raise ValueError(f"{name} must be non-empty")


@dataclass(frozen=True, slots=True)
class ExternalUserProfile:
    external_user_id: str
    email: str

    def __post_init__(self) -> None:
        _require_non_empty(self.external_user_id, name="external_user_id")
        _require_non_empty(self.email, name="email")


@dataclass(frozen=True, slots=True)
class ExternalIdentity:
    external_user_id: str
    bootstrap_profile: ExternalUserProfile | None = None

    def __post_init__(self) -> None:
        _require_non_empty(self.external_user_id, name="external_user_id")
        if (
            self.bootstrap_profile is not None
            and self.bootstrap_profile.external_user_id != self.external_user_id
        ):
            raise ValueError(
                "bootstrap profile must describe the same external user"
            )


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    internal_user_id: UUID
