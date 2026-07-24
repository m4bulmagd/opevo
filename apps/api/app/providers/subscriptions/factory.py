from app.core.config import Settings
from app.providers.subscriptions.base import SubscriptionProvider
from app.providers.subscriptions.fake import FakeSubscriptionProvider
from app.providers.subscriptions.stripe import StripeSubscriptionProvider


def build_subscription_provider(settings: Settings) -> SubscriptionProvider:
    if settings.billing_mode == "stripe":
        return StripeSubscriptionProvider(secret_key=settings.stripe_secret_key)
    return FakeSubscriptionProvider()
