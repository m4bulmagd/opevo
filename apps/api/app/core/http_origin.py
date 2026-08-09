from dataclasses import dataclass
import ipaddress
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class HttpOrigin:
    scheme: str
    host: str
    port: int


def parse_http_origin(value: str) -> HttpOrigin:
    if (
        not value
        or value != value.strip()
        or any(character.isspace() or ord(character) == 127 for character in value)
    ):
        raise ValueError("invalid URL")

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("invalid URL") from None

    scheme = parsed.scheme.lower()
    host = parsed.hostname
    if (
        scheme not in {"http", "https"}
        or host is None
        or any(character.isspace() for character in host)
        or "\\" in parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("invalid URL")

    effective_port = port if port is not None else (443 if scheme == "https" else 80)
    return HttpOrigin(scheme=scheme, host=host.lower(), port=effective_port)


def _has_unsafe_url_character(value: str) -> bool:
    return any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in value
    )


def _canonical_origin_host(host: str) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if ":" in host:
            raise ValueError("invalid canonical HTTP origin") from None
        try:
            ascii_host = host.encode("idna").decode("ascii").lower()
        except UnicodeError:
            raise ValueError("invalid canonical HTTP origin") from None
        labels = ascii_host.split(".")
        if not all(
            label
            and len(label) <= 63
            and label[0] != "-"
            and label[-1] != "-"
            and all(character.isalnum() or character == "-" for character in label)
            for label in labels
        ):
            raise ValueError("invalid canonical HTTP origin")
        return ascii_host
    canonical = address.compressed.lower()
    return f"[{canonical}]" if address.version == 6 else canonical


def parse_canonical_http_origin(value: str) -> str:
    error = ValueError("invalid canonical HTTP origin")
    if (
        not value
        or value != value.strip()
        or _has_unsafe_url_character(value)
        or "\\" in value
        or "*" in value
    ):
        raise error
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise error from None
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise error
    host = _canonical_origin_host(parsed.hostname)
    default_port = 443 if parsed.scheme == "https" else 80
    port_suffix = "" if port is None or port == default_port else f":{port}"
    canonical = f"{parsed.scheme}://{host}{port_suffix}"
    if canonical != value:
        raise error
    return canonical


def parse_canonical_http_origins(value: str | None) -> tuple[str, ...]:
    if value is None:
        raise ValueError("missing canonical HTTP origins")
    origins = tuple(parse_canonical_http_origin(item) for item in value.split(","))
    if len(origins) != len(set(origins)):
        raise ValueError("duplicate canonical HTTP origin")
    return origins


def validate_absolute_https_url(value: str) -> None:
    error = ValueError("invalid HTTPS URL")
    if (
        not value
        or value != value.strip()
        or _has_unsafe_url_character(value)
        or "\\" in value
        or "*" in value
        or "#" in value
    ):
        raise error
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        raise error from None
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise error
    try:
        _canonical_origin_host(parsed.hostname)
    except ValueError:
        raise error from None
