from dataclasses import dataclass
from datetime import datetime
import re
from typing import Literal, Protocol
import unicodedata


CarrierCode = Literal["orange", "sfr", "bouygues", "free", "other"]
CarrierLookupErrorCode = Literal["retryable", "terminal"]


@dataclass(frozen=True, slots=True)
class CarrierLookupResult:
    normalized_number: str
    country_code: str
    carrier_name: str | None
    normalized_carrier: CarrierCode
    number_type: str | None
    looked_up_at: datetime


class CarrierLookupError(RuntimeError):
    def __init__(self, code: CarrierLookupErrorCode) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = code == "retryable"


class CarrierLookupProvider(Protocol):
    async def lookup(self, e164: str) -> CarrierLookupResult:
        raise NotImplementedError


def normalize_carrier_name(carrier_name: object | None) -> CarrierCode:
    if carrier_name is None:
        return "other"
    if not isinstance(carrier_name, str):
        raise ValueError("Malformed carrier name")
    folded = "".join(
        character
        for character in unicodedata.normalize("NFKD", carrier_name)
        if not unicodedata.combining(character)
    ).casefold()
    tokens = set(re.findall(r"[a-z0-9]+", folded))
    if "orange" in tokens:
        return "orange"
    if "sfr" in tokens or {
        "societe",
        "francaise",
        "radiotelephone",
    } <= tokens:
        return "sfr"
    if "bouygues" in tokens:
        return "bouygues"
    if tokens & {"free", "iliad"}:
        return "free"
    return "other"


def normalize_number_type(number_type: object | None) -> str | None:
    if number_type is None:
        return None
    if not isinstance(number_type, str):
        raise ValueError("Malformed number type")
    if not number_type:
        return None
    folded = number_type.casefold().replace("-", "_").replace(" ", "_")
    if "mobile" in folded:
        return "mobile"
    if folded in {"fixed", "landline", "fixed_line"}:
        return "fixed"
    if "voip" in folded:
        return "voip"
    return "other"
