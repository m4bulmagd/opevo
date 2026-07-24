from app.providers.subscriptions.base import SubscriptionProvider, SubscriptionProviderError


class FakeSubscriptionProvider(SubscriptionProvider):
    async def cancel_immediately(self, subscription_id: str) -> None:
        if not isinstance(subscription_id, str) or not subscription_id.strip():
            raise SubscriptionProviderError(
                "provider_terminal",
                error_class="validation",
            )
