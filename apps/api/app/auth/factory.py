from app.auth.providers.base import AuthProvider
from app.auth.providers.clerk import ClerkAuthProvider
from app.auth.providers.local import LocalAuthProvider
from app.auth.providers.supabase import SupabaseAuthProvider
from app.auth.jwks import (
    JwksSigningKeyResolver,
    SigningKeyResolver,
    StaticSigningKeyResolver,
)
from app.core.clerk_verification_source import select_clerk_verification_source
from app.core.config import Settings
from app.core.http_origin import parse_canonical_http_origins
from app.core.observability import Observability


def build_auth_provider(
    *,
    settings: Settings,
    observability: Observability,
) -> AuthProvider:
    if settings.auth_provider == "local":
        return LocalAuthProvider(token=settings.local_auth_token)
    if settings.auth_provider == "supabase":
        supabase_base_url = settings.supabase_url.rstrip("/")
        return SupabaseAuthProvider(
            issuer=f"{supabase_base_url}/auth/v1",
            audience=settings.supabase_jwt_audience,
            signing_key_resolver=JwksSigningKeyResolver(
                jwks_url=(
                    f"{supabase_base_url}/auth/v1/.well-known/jwks.json"
                ),
                cache_ttl_seconds=300.0,
                stale_grace_seconds=600.0,
                connect_timeout_seconds=0.5,
                read_timeout_seconds=1.0,
                pool_timeout_seconds=0.25,
                total_timeout_seconds=2.0,
                observability=observability,
                allowed_algorithms=frozenset({"ES256", "RS256"}),
            ),
            observability=observability,
        )
    authorized_parties = frozenset(
        parse_canonical_http_origins(settings.clerk_authorized_parties)
    )
    verification_source = select_clerk_verification_source(
        jwt_key=settings.clerk_jwt_key,
        jwks_url=settings.clerk_jwks_url,
    )
    if verification_source is None:
        raise RuntimeError(
            "Missing or invalid required runtime settings: "
            "exactly one of CLERK_JWT_KEY or CLERK_JWKS_URL"
        )
    if verification_source.kind == "static":
        resolver: SigningKeyResolver = StaticSigningKeyResolver(
            verification_source.value
        )
    else:
        resolver = JwksSigningKeyResolver(
            jwks_url=verification_source.value,
            cache_ttl_seconds=settings.clerk_jwks_cache_ttl_seconds,
            stale_grace_seconds=settings.clerk_jwks_stale_grace_seconds,
            connect_timeout_seconds=settings.clerk_jwks_connect_timeout_seconds,
            read_timeout_seconds=settings.clerk_jwks_read_timeout_seconds,
            pool_timeout_seconds=settings.clerk_jwks_pool_timeout_seconds,
            total_timeout_seconds=settings.clerk_jwks_total_timeout_seconds,
            observability=observability,
        )
    return ClerkAuthProvider(
        settings=settings,
        authorized_parties=authorized_parties,
        signing_key_resolver=resolver,
        observability=observability,
    )
