from app.models.phone_number import PhoneNumber
from app.models.phone_number_provisioning import PhoneNumberProvisioning


def number_is_provisioned(
    *,
    provisioning: PhoneNumberProvisioning | None,
    phone_number: PhoneNumber | None,
) -> bool:
    return bool(
        provisioning is not None
        and provisioning.status == "succeeded"
        and phone_number is not None
        and provisioning.phone_number_id is not None
        and provisioning.phone_number_id == phone_number.id
        and phone_number.provider_number_id is not None
        and phone_number.provider_number_id.strip()
    )
