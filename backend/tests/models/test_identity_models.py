import pytest

from app.models import normalize_username, sanitize_audit_changes


def test_normalize_username_is_case_insensitive() -> None:
    assert normalize_username("Admin.User") == "admin.user"
    assert normalize_username("  Admin.User  ") == "admin.user"


def test_sanitize_audit_changes_rejects_password_keys() -> None:
    with pytest.raises(ValueError, match="sensitive key"):
        sanitize_audit_changes({"password": "secret"})

    with pytest.raises(ValueError, match="sensitive key"):
        sanitize_audit_changes({"nested": {"refresh_token": "abc"}})

    with pytest.raises(ValueError, match="sensitive key"):
        sanitize_audit_changes({"meeting_password": "meet-secret"})

    with pytest.raises(ValueError, match="sensitive key"):
        sanitize_audit_changes({"meeting_password_encrypted": "enc:v1:xx"})

    with pytest.raises(ValueError, match="sensitive key"):
        sanitize_audit_changes({"contact_phone": "13800138000"})


def test_sanitize_audit_changes_allows_safe_keys() -> None:
    result = sanitize_audit_changes({"role": "system_admin", "is_active": True})

    assert result == {"role": "system_admin", "is_active": True}
