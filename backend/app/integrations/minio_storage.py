from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from io import BytesIO
from urllib.parse import urlparse

from minio import Minio
from minio.error import S3Error

from app.core.config import get_settings


class StorageError(Exception):
    pass


@dataclass(frozen=True)
class StoredObject:
    bucket: str
    key: str
    size: int


@lru_cache
def get_minio_client() -> Minio:
    settings = get_settings()
    endpoint = settings.MINIO_ENDPOINT.strip()
    # allow http://localhost:9000 or localhost:9000
    if "://" in endpoint:
        parsed = urlparse(endpoint)
        host = parsed.netloc or parsed.path
        secure = parsed.scheme == "https"
    else:
        host = endpoint
        secure = settings.MINIO_SECURE
    return Minio(
        host,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=secure,
    )


def ensure_bucket() -> str:
    settings = get_settings()
    client = get_minio_client()
    bucket = settings.MINIO_BUCKET
    try:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
    except S3Error as exc:
        raise StorageError(f"minio bucket check failed: {exc}") from exc
    return bucket


def put_bytes(
    *,
    key: str,
    data: bytes,
    content_type: str,
) -> StoredObject:
    bucket = ensure_bucket()
    client = get_minio_client()
    try:
        client.put_object(
            bucket,
            key,
            BytesIO(data),
            length=len(data),
            content_type=content_type or "application/octet-stream",
        )
    except S3Error as exc:
        raise StorageError(f"minio upload failed: {exc}") from exc
    return StoredObject(bucket=bucket, key=key, size=len(data))


def get_bytes(key: str) -> bytes:
    bucket = ensure_bucket()
    client = get_minio_client()
    try:
        response = client.get_object(bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()
    except S3Error as exc:
        raise StorageError(f"minio download failed: {exc}") from exc


def presigned_get_url(key: str, *, expires_seconds: int = 600) -> str:
    bucket = ensure_bucket()
    client = get_minio_client()
    try:
        return client.presigned_get_object(
            bucket,
            key,
            expires=timedelta(seconds=expires_seconds),
        )
    except S3Error as exc:
        raise StorageError(f"minio presign failed: {exc}") from exc
