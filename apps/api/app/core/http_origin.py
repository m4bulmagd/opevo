from dataclasses import dataclass
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
