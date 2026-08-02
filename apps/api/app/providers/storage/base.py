from dataclasses import dataclass


@dataclass(frozen=True)
class StoredObject:
    object_key: str
    url: str


class StorageProvider:
    async def upload_bytes(self, *, object_key: str, data: bytes, content_type: str) -> StoredObject:
        raise NotImplementedError

    async def get_download_url(self, *, object_key: str) -> str | None:
        raise NotImplementedError

    async def delete_object(self, *, object_key: str) -> None:
        raise NotImplementedError
