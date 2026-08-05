from app.workers.outbox.account_deactivation import deliver_account_deactivation
from app.workers.outbox.customer_dispatch import deliver_livekit_dispatch
from app.workers.outbox.phone import deliver_phone_provision, deliver_phone_routing
from app.workers.outbox.post_call import (
    deliver_recording_reconcile,
    deliver_summary_generate,
)
from app.workers.outbox.provider_cleanup import deliver_provider_cleanup
from app.workers.outbox.verification_dispatch import (
    deliver_livekit_verification_dispatch,
)


DEFAULT_OUTBOX_HANDLERS = {
    "account.deactivate": deliver_account_deactivation,
    "provider.cleanup": deliver_provider_cleanup,
    "phone.provision": deliver_phone_provision,
    "phone.enable": deliver_phone_routing,
    "phone.disable": deliver_phone_routing,
    "livekit.dispatch": deliver_livekit_dispatch,
    "livekit.verification_dispatch": deliver_livekit_verification_dispatch,
    "summary.generate": deliver_summary_generate,
    "recording.reconcile": deliver_recording_reconcile,
}
