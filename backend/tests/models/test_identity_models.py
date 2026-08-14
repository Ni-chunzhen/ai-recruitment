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


def test_sanitize_audit_changes_allows_safe_keys() -> None:
    result = sanitize_audit_changes({"role": "system_admin", "is_active": True})

    assert result == {"role": "system_admin", "is_active": True}
