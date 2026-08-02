from app.core.provider_failures import ProviderFailure
from app.providers.subscriptions.base import SubscriptionProvider


class FakeSubscriptionProvider(SubscriptionProvider):
    async def cancel_immediately(self, subscription_id: str) -> None:
        if not isinstance(subscription_id, str) or not subscription_id.strip():
            raise ProviderFailure(
                provider="fake",
                operation="validate",
                disposition="terminal",
                error_class="validation",
            )
