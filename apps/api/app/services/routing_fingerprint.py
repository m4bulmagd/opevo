import hashlib
import json

from app.models.business_profile import BusinessProfile
from app.models.phone_number import PhoneNumber


def routing_fingerprint(
    profile: BusinessProfile,
    phone_number: PhoneNumber | None,
) -> str:
    payload = {
        "existing_phone_e164": profile.existing_phone_e164,
        "confirmed_carrier": profile.confirmed_carrier,
        "presvo_phone_e164": phone_number.e164 if phone_number is not None else None,
        "routing_revision": profile.routing_revision,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
