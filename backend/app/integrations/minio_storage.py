from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from io import BytesIO
from urllib.parse import urlparse

from minio import Minio
from minio.error import S3Error

from app.services.integration_config import (
    effective_minio_access_key,
    effective_minio_bucket,
    effective_minio_endpoint,
    effective_minio_presign_seconds,
    effective_minio_secret_key,
    effective_minio_secure,
)


class StorageError(Exception):
    pass


@dataclass(frozen=True)
class StoredObject:
    bucket: str
    key: str
    size: int


def effective_bucket_name() -> str:
    return effective_minio_bucket()


@lru_cache
def get_minio_client() -> Minio:
    endpoint = effective_minio_endpoint()
    # allow http://localhost:9000 or localhost:9000
    if "://" in endpoint:
        parsed = urlparse(endpoint)
        host = parsed.netloc or parsed.path
        secure = parsed.scheme == "https"
    else:
        host = endpoint
        secure = effective_minio_secure()
    return Minio(
        host,
        access_key=effective_minio_access_key(),
        secret_key=effective_minio_secret_key(),
        secure=secure,
    )


def ensure_bucket() -> str:
    client = get_minio_client()
    bucket = effective_bucket_name()
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


def presigned_get_url(key: str, *, expires_seconds: int | None = None) -> str:
    bucket = ensure_bucket()
    client = get_minio_client()
    ttl = (
        expires_seconds
        if expires_seconds is not None
        else effective_minio_presign_seconds()
    )
    try:
        return client.presigned_get_object(
            bucket,
            key,
            expires=timedelta(seconds=ttl),
        )
    except S3Error as exc:
        raise StorageError(f"minio presign failed: {exc}") from exc
