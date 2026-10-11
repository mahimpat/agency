"""Where files live: letter PDFs and HTML, and photos of children's replies.

Local folder by default (DATA_DIR). Set STORAGE_BUCKET (plus endpoint and keys)
to use any S3-compatible store instead, such as Supabase Storage, Cloudflare R2
or AWS S3, so files survive server rebuilds and are backed up.

Files are addressed by keys like "letters/12/letter-5.pdf" or "uploads/ab12.jpg".
"""

import contextlib
import mimetypes
import tempfile
from pathlib import Path
from typing import Iterator

from .config import Settings


class LocalStorage:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError(f"Bad storage key: {key}")
        return path

    def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    @contextlib.contextmanager
    def local_path(self, key: str) -> Iterator[Path]:
        yield self._path(key)


class S3Storage:
    """Any S3-compatible bucket. Objects are private; the app serves them after checking access."""

    def __init__(self, bucket: str, client):
        self.bucket = bucket
        self.client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> "S3Storage":
        import boto3  # optional dependency: pip install ".[s3]"
        client = boto3.client(
            "s3", endpoint_url=settings.storage_endpoint or None, region_name=settings.storage_region or None,
            aws_access_key_id=settings.storage_access_key or None,
            aws_secret_access_key=settings.storage_secret_key or None)
        return cls(settings.storage_bucket, client)

    def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data,
                               ContentType=content_type or mimetypes.guess_type(key)[0] or "application/octet-stream")

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:  # noqa: BLE001 - botocore raises ClientError (404) for a missing object
            return False

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    @contextlib.contextmanager
    def local_path(self, key: str) -> Iterator[Path]:
        """Some tools (the AI image reader, the mail API) need a file on disk: use a temporary copy."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / Path(key).name
            path.write_bytes(self.get(key))
            yield path


Storage = LocalStorage | S3Storage


def get_storage(settings: Settings) -> Storage:
    cached = getattr(settings, "_storage", None)
    if cached is None:
        cached = S3Storage.from_settings(settings) if settings.storage_bucket else LocalStorage(settings.data_dir)
        settings._storage = cached
    return cached


def content_type(key: str) -> str:
    return mimetypes.guess_type(key)[0] or "application/octet-stream"
