"""Injectable connectivity probes for Dify / MinIO / Mail (Console).

Never enqueues work, never runs Dify workflows, never MinIO put/get/presign.
Responses are only ``ok`` / ``error_code`` / ``latency_ms`` — no bodies, headers, or secrets.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

CONNECTIVITY_TIMEOUT_SECONDS = 5.0

ERROR_NOT_CONFIGURED = "not_configured"
ERROR_TIMEOUT = "timeout"
ERROR_UNREACHABLE = "unreachable"
ERROR_AUTH_FAILED = "auth_failed"
ERROR_HTTP = "http_error"
ERROR_BUCKET_MISSING = "bucket_missing"
ERROR_PROBE_FAILED = "probe_failed"


@dataclass(frozen=True)
class ConnectivityResult:
    ok: bool
    error_code: str | None
    latency_ms: int


class SupportsHttpRequest(Protocol):
    async def request(self, method: str, url: str, **kwargs: Any) -> Any: ...


class SupportsBucketExists(Protocol):
    def bucket_exists(self, bucket: str) -> bool: ...


def _latency_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _classify_exc(exc: BaseException) -> str:
    name = type(exc).__name__.lower()
    text = str(type(exc))  # type only — never use exception message (may hold secrets)
    if "timeout" in name or "timeout" in text.lower():
        return ERROR_TIMEOUT
    if any(
        token in name
        for token in ("connect", "network", "unreachable", "refused", "dns")
    ):
        return ERROR_UNREACHABLE
    return ERROR_PROBE_FAILED


async def probe_dify(
    *,
    api_base_url: str,
    api_key: str,
    http_client: SupportsHttpRequest | None = None,
    timeout_seconds: float = CONNECTIVITY_TIMEOUT_SECONDS,
) -> ConnectivityResult:
    """Minimal safe probe against Dify base URL. Discards body; never returns it."""
    base = (api_base_url or "").strip().rstrip("/")
    key = (api_key or "").strip()
    if not base or not key:
        return ConnectivityResult(
            ok=False, error_code=ERROR_NOT_CONFIGURED, latency_ms=0
        )

    started = time.perf_counter()
    client = http_client
    owns_client = False
    try:
        if client is None:
            import httpx

            client = httpx.AsyncClient(timeout=timeout_seconds)
            owns_client = True
        # Auth header used only for the probe; never logged / never returned.
        response = await client.request(
            "GET",
            base,
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout_seconds,
        )
        status = int(getattr(response, "status_code", 0) or 0)
        # Intentionally do not read response.text / content.
        if status in (401, 403):
            return ConnectivityResult(
                ok=False,
                error_code=ERROR_AUTH_FAILED,
                latency_ms=_latency_ms(started),
            )
        if 200 <= status < 500:
            # 2xx–4xx (except auth) treated as reachable control-plane response.
            if status >= 400:
                return ConnectivityResult(
                    ok=False,
                    error_code=ERROR_HTTP,
                    latency_ms=_latency_ms(started),
                )
            return ConnectivityResult(
                ok=True, error_code=None, latency_ms=_latency_ms(started)
            )
        return ConnectivityResult(
            ok=False, error_code=ERROR_HTTP, latency_ms=_latency_ms(started)
        )
    except Exception as exc:  # noqa: BLE001 — map to stable codes only
        code = _classify_exc(exc)
        logger.warning("dify connectivity probe failed error_code=%s", code)
        return ConnectivityResult(
            ok=False, error_code=code, latency_ms=_latency_ms(started)
        )
    finally:
        if owns_client and client is not None:
            aclose = getattr(client, "aclose", None)
            if callable(aclose):
                await aclose()


def probe_minio(
    *,
    endpoint: str,
    access_key: str,
    secret_key: str,
    bucket: str,
    secure: bool = False,
    minio_client: SupportsBucketExists | None = None,
) -> ConnectivityResult:
    """Endpoint/bucket existence only — no put/get/presign."""
    ep = (endpoint or "").strip()
    ak = (access_key or "").strip()
    sk = (secret_key or "").strip()
    bkt = (bucket or "").strip()
    if not ep or not ak or not sk or not bkt:
        return ConnectivityResult(
            ok=False, error_code=ERROR_NOT_CONFIGURED, latency_ms=0
        )

    started = time.perf_counter()
    try:
        client = minio_client
        if client is None:
            from urllib.parse import urlparse

            from minio import Minio

            if "://" in ep:
                parsed = urlparse(ep)
                host = parsed.netloc or parsed.path
                use_secure = parsed.scheme == "https"
            else:
                host = ep
                use_secure = secure
            client = Minio(host, access_key=ak, secret_key=sk, secure=use_secure)

        exists = bool(client.bucket_exists(bkt))
        if not exists:
            return ConnectivityResult(
                ok=False,
                error_code=ERROR_BUCKET_MISSING,
                latency_ms=_latency_ms(started),
            )
        return ConnectivityResult(
            ok=True, error_code=None, latency_ms=_latency_ms(started)
        )
    except Exception as exc:  # noqa: BLE001
        code = _classify_exc(exc)
        logger.warning("minio connectivity probe failed error_code=%s", code)
        return ConnectivityResult(
            ok=False, error_code=code, latency_ms=_latency_ms(started)
        )


def probe_mail_console() -> ConnectivityResult:
    """Console-only no-op success. No SMTP."""
    return ConnectivityResult(ok=True, error_code=None, latency_ms=0)
