"""Audit field-name scrub (Task 2) — sensitive keys redacted even for plain values."""

from __future__ import annotations


def test_audit_scrubs_by_field_name() -> None:
    from app.services.audit import _scrub_value

    scrubbed = _scrub_value(
        {
            "api_key": "literally-plain-string",
            "access_key": "minioadmin",
            "secret_key": "minioadmin",
            "password": "hunter2",
            "provider": "dify",
            "updated_keys": ["api_base_url", "api_key"],
            "nested": {"secret": "still-plain", "endpoint": "127.0.0.1:9000"},
        }
    )
    assert scrubbed["api_key"] == "[redacted]"
    assert scrubbed["access_key"] == "[redacted]"
    assert scrubbed["secret_key"] == "[redacted]"
    assert scrubbed["password"] == "[redacted]"
    assert scrubbed["provider"] == "dify"
    assert scrubbed["updated_keys"] == ["api_base_url", "api_key"]
    assert scrubbed["nested"]["secret"] == "[redacted]"
    assert scrubbed["nested"]["endpoint"] == "127.0.0.1:9000"


def test_audit_scrubs_suffix_sensitive_keys() -> None:
    from app.services.audit import _scrub_value

    scrubbed = _scrub_value(
        {
            "dify_api_key": "x",
            "minio_secret_key": "y",
            "secret_ciphertext": "enc:v1:abc",
            "secret_keys_updated": ["api_key"],
        }
    )
    assert scrubbed["dify_api_key"] == "[redacted]"
    assert scrubbed["minio_secret_key"] == "[redacted]"
    assert scrubbed["secret_ciphertext"] == "[redacted]"
    # metadata list of key names must remain (not a secret value payload)
    assert scrubbed["secret_keys_updated"] == ["api_key"]


def test_audit_still_scrubs_value_markers() -> None:
    from app.services.audit import _scrub_value

    scrubbed = _scrub_value(
        {
            "debug": "prefix enc:v1:ciphertext-blob",
            "note": "Bearer abc.def",
            "status": "ok",
        }
    )
    assert scrubbed["debug"] == "[redacted]"
    assert scrubbed["note"] == "[redacted]"
    assert scrubbed["status"] == "ok"
